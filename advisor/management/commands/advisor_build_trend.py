from __future__ import annotations

import json
import math
from pathlib import Path
from datetime import date
from typing import Dict, List, Set, Iterable, Optional

import pandas as pd
import yfinance as yf
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from advisor.models_trend import TrendResult
from advisor.models import WatchEntry

# 価格フォールバック（任意）
try:
    from advisor.models_cache import PriceCache  # ある場合だけ使う
except Exception:
    PriceCache = None  # type: ignore

from portfolio.models import Holding

User = get_user_model()

ALIAS_JSON = Path("media/advisor/symbol_alias.json")  # ここに任意の別名表を置く

# ---------- symbol helpers ----------

def _load_alias_map() -> Dict[str, str]:
    """任意のシンボル別名マップ。例: {"167A":"1671.T"}"""
    try:
        if ALIAS_JSON.exists():
            with open(ALIAS_JSON, "r", encoding="utf-8") as f:
                m = json.load(f)
                return {str(k).upper(): str(v).upper() for k, v in m.items()}
    except Exception:
        pass
    return {}

def _symbol_candidates(raw: str, alias_map: Dict[str, str]) -> List[str]:
    """
    取得候補を複数用意（順に試す）。絶対に“即スキップ”しない。
    優先度: alias -> raw -> raw+'.T' -> 数字部+'.T'
    """
    t = (raw or "").strip().upper()
    cands: List[str] = []
    if not t:
        return cands

    if t in alias_map:
        cands.append(alias_map[t])

    cands.append(t)  # そのままも試す（例: 海外ETF等）
    if not t.endswith(".T"):
        cands.append(f"{t}.T")

    # “167A” → “167” のように末尾英字を外してから .T
    if t[:-1].isdigit() and t[-1].isalpha():
        cands.append(f"{t[:-1]}.T")

    # 重複除去を維持順で
    seen: Set[str] = set()
    uniq = []
    for s in cands:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq

# ---------- calc helpers ----------

def _weekly_trend_from_prices(df: pd.DataFrame) -> str:
    if df is None or df.empty or "Close" not in df.columns:
        return "flat"
    tail = df.tail(20)
    if len(tail) < 5:
        return "flat"
    y = tail["Close"].astype(float).values
    x = range(len(y))
    x_mean = sum(x)/len(x); y_mean = sum(y)/len(y)
    num = sum((xi-x_mean)*(yi-y_mean) for xi, yi in zip(x, y))
    den = sum((xi-x_mean)**2 for xi in x) or 1.0
    slope = num/den
    return "up" if slope > 0 else ("down" if slope < 0 else "flat")

def _slope_annual_from_prices(df: pd.DataFrame) -> float:
    if df is None or df.empty or "Close" not in df.columns:
        return 0.0
    close = df["Close"].astype(float)
    if len(close) < 5:
        return 0.0
    logret = (close/close.shift(1)).apply(lambda v: math.log(v) if v and v > 0 else 0.0).dropna()
    if len(logret) == 0:
        return 0.0
    mu = float(logret.mean())
    return float((math.exp(mu*250) - 1.0))

def _confidence_from_df(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.5
    n = len(df)
    base = 0.3 + min(0.6, n/200.0)
    return float(round(max(0.3, min(0.9, base)), 3))

def _collect_targets(user) -> List[str]:
    s: Set[str] = set()
    for w in WatchEntry.objects.filter(user=user, status=WatchEntry.STATUS_ACTIVE).only("ticker"):
        if w.ticker:
            s.add(w.ticker.strip().upper())
    for h in Holding.objects.filter(user=user).only("ticker"):
        if h.ticker:
            s.add(h.ticker.strip().upper())
    return list(s)[:80]

# ---------- price fallbacks ----------

def _fallback_price(tkr: str, user) -> Optional[int]:
    """Yahoo失敗時に最後の手段として使う価格"""
    # 1) PriceCache
    if PriceCache is not None:
        pc = PriceCache.objects.filter(ticker=tkr.upper()).first()
        if pc and pc.last_price is not None:
            try:
                return int(pc.last_price)
            except Exception:
                pass
    # 2) Holding.last_price（ユーザー保有のもの）
    h = Holding.objects.filter(user=user, ticker=tkr.upper()).only("last_price").first()
    if h and h.last_price is not None:
        try:
            return int(round(float(h.last_price)))
        except Exception:
            pass
    return None

# ---------- command ----------

class Command(BaseCommand):
    help = "Build TrendResult for active/watch/holdings. Never skip symbols (uses aliases/fallbacks)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=60, help="lookback days")
        parser.add_argument("--user-id", type=int, default=None, help="target user id")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        days: int = int(opts.get("days") or 60)
        user_id = opts.get("user_id")
        dry = bool(opts.get("dry_run"))

        user = User.objects.filter(id=user_id).first() if user_id else User.objects.first()
        if not user:
            self.stdout.write(self.style.ERROR("No user found")); return

        alias_map = _load_alias_map()
        targets = _collect_targets(user)
        self.stdout.write(f"[trend] days={days} targets={len(targets)}")

        for tkr in targets:
            # 1) 価格取得トライ（候補を順に）
            df = None
            tried = []
            for sym in _symbol_candidates(tkr, alias_map):
                tried.append(sym)
                try:
                    df = yf.download(sym, period=f"{max(days+10,70)}d", interval="1d", progress=False, auto_adjust=False)
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"{tkr} ({sym}) download err: {e}"))
                    df = None
                if df is not None and not df.empty:
                    break

            # 2) Yahooで取れなかった場合でも“絶対にスキップしない”
            if df is None or df.empty:
                # フォールバック価格
                price = _fallback_price(tkr, user)
                asof = date.today()  # 今日で固定（取得不可だが更新はする）
                weekly_trend = "flat"
                slope_annual = 0.0
                confidence = 0.5
                close_price = price
            else:
                df = df.tail(days)
                asof_pd = df.index[-1]
                asof = date(asof_pd.year, asof_pd.month, asof_pd.day)
                weekly_trend = _weekly_trend_from_prices(df)
                slope_annual = _slope_annual_from_prices(df)
                confidence = _confidence_from_df(df)
                close_price = float(df["Close"].iloc[-1]) if "Close" in df.columns else None

            entry_price_hint = int(round(close_price)) if close_price else _fallback_price(tkr, user)

            if entry_price_hint is None:
                # 本当に何も無い時でもダミーで作る（板生成のため）
                entry_price_hint = 3000

            if dry:
                self.stdout.write(f"DRY save {tkr} asof={asof} trend={weekly_trend} slope={round(slope_annual,4)} conf={confidence} tried={tried}")
                continue

            try:
                with transaction.atomic():
                    TrendResult.objects.update_or_create(
                        user=user,
                        ticker=tkr.upper(),
                        asof=asof,
                        defaults={
                            "name": tkr.upper(),
                            "close_price": int(round(close_price)) if close_price else None,
                            "entry_price_hint": int(entry_price_hint),
                            "weekly_trend": weekly_trend,
                            "win_prob": None,
                            "theme_label": "",
                            "theme_score": None,
                            "overall_score": None,
                            "size_mult": None,
                            "notes": {"tried": tried},
                            "slope_annual": float(slope_annual),
                            "confidence": float(confidence),
                            "window_days": int(days),
                        },
                    )
                self.stdout.write(self.style.SUCCESS(f"saved trend: {tkr} asof={asof}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"save error {tkr}: {e}"))

        self.stdout.write(self.style.SUCCESS("trend build done."))