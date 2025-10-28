from __future__ import annotations

import json
import math
import time
from pathlib import Path
from datetime import date
from typing import Dict, List, Set, Optional

import numpy as np
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

ALIAS_JSON = Path("media/advisor/symbol_alias.json")  # 別名表: {"167A":"1671.T"} など


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
    if len(t) >= 2 and t[:-1].isdigit() and t[-1].isalpha():
        cands.append(f"{t[:-1]}.T")

    # 重複除去（順序保持）
    seen: Set[str] = set()
    uniq: List[str] = []
    for s in cands:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq


# ---------- calc helpers ----------

def _close_series(df: pd.DataFrame) -> Optional[pd.Series]:
    """
    yfinanceの返りが環境によってはDataFrame/Seriesで揺れるので、必ずSeries化する。
    """
    if df is None or df.empty:
        return None
    if "Close" not in df.columns:
        return None
    close = df["Close"]
    # CloseがDataFrame（列複数）のことが稀にあるので最初の列に潰す
    if isinstance(close, pd.DataFrame):
        if close.shape[1] == 0:
            return None
        close = close.iloc[:, 0]
    # float化
    try:
        close = close.astype(float)
    except Exception:
        return None
    return close

def _weekly_trend_from_prices(df: pd.DataFrame) -> str:
    close = _close_series(df)
    if close is None or close.empty:
        return "flat"
    tail = close.tail(20)
    if len(tail) < 5:
        return "flat"
    y = tail.values
    x = np.arange(len(y), dtype=float)
    x_mean = x.mean(); y_mean = y.mean()
    num = float(((x - x_mean) * (y - y_mean)).sum())
    den = float(((x - x_mean) ** 2).sum()) or 1.0
    slope = num / den
    return "up" if slope > 0 else ("down" if slope < 0 else "flat")

def _slope_annual_from_prices(df: pd.DataFrame) -> float:
    """
    連続複利ベースの年率化リターンを近似。
    ロバスト化: log(close).diff() を使用し、0/負値はclipで回避。
    """
    close = _close_series(df)
    if close is None or close.empty:
        return 0.0
    if len(close) < 5:
        return 0.0
    logret = np.log(close.clip(lower=1e-9)).diff().dropna()
    if logret.empty:
        return 0.0
    mu = float(logret.mean())
    return float(np.exp(mu * 250) - 1.0)  # 営業日換算

def _confidence_from_df(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.5
    n = len(df)
    base = 0.3 + min(0.6, n / 200.0)
    return float(round(max(0.3, min(0.9, base)), 3))

def _overall_score_from(slope_annual: float, confidence: float) -> int:
    """
    slope_annual(年率リターン, -1〜+∞想定)を0-1に圧縮し、confidenceと合成して0-100点化。
    例: slope=0% → 50点、+50% → ~81点、-50% → ~19点（confidence 0.5時）
    """
    # シグモイドで 0〜1 へ
    s_norm = 1.0 / (1.0 + math.exp(-3.0 * float(slope_annual)))  # 0〜1
    s_norm = max(0.0, min(1.0, s_norm))
    conf = max(0.0, min(1.0, float(confidence or 0.5)))
    score = (s_norm * 0.7) + (conf * 0.3)
    return int(round(score * 100))

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
            self.stdout.write(self.style.ERROR("No user found"))
            return

        alias_map = _load_alias_map()
        targets = _collect_targets(user)
        self.stdout.write(f"[trend] days={days} targets={len(targets)}")

        for tkr in targets:
            # 1) 価格取得トライ（候補を順に）
            df = None
            tried: List[str] = []
            for sym in _symbol_candidates(tkr, alias_map):
                tried.append(sym)
                try:
                    df = yf.download(
                        sym,
                        period=f"{max(days + 10, 70)}d",
                        interval="1d",
                        progress=False,
                        auto_adjust=False,
                    )
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"{tkr} ({sym}) download err: {e}"))
                    df = None
                # Yahoo側の連続アクセス対策（成功/失敗どちらでも1秒休む）
                time.sleep(1)
                if df is not None and not df.empty:
                    break

            # 2) Yahooで取れなくても“絶対にスキップしない” → フォールバックで埋める
            if df is None or df.empty:
                asof = date.today()
                weekly_trend = "flat"
                slope_annual = 0.0
                confidence = 0.5
                close_price = _fallback_price(tkr, user)
            else:
                df = df.tail(days).copy()
                # asof
                idx = df.index[-1]
                asof = date(idx.year, idx.month, idx.day)
                weekly_trend = _weekly_trend_from_prices(df)
                slope_annual = _slope_annual_from_prices(df)
                confidence = _confidence_from_df(df)
                cs = _close_series(df)
                close_price = float(cs.iloc[-1]) if cs is not None and not cs.empty else None

            entry_price_hint = (
                int(round(close_price)) if close_price is not None else _fallback_price(tkr, user)
            )
            if entry_price_hint is None:
                entry_price_hint = 3000  # 最後の最後のダミー

            # ★ 必ず overall_score を埋める（フォールバックでも）
            overall_score = _overall_score_from(slope_annual, confidence)

            if dry:
                self.stdout.write(
                    f"DRY save {tkr} asof={asof} trend={weekly_trend} "
                    f"slope={round(float(slope_annual), 4)} conf={confidence} "
                    f"score={overall_score} tried={tried}"
                )
                continue

            try:
                with transaction.atomic():
                    TrendResult.objects.update_or_create(
                        user=user,
                        ticker=tkr.upper(),
                        asof=asof,
                        defaults={
                            "name": tkr.upper(),
                            "close_price": int(round(close_price)) if close_price is not None else None,
                            "entry_price_hint": int(entry_price_hint),
                            "weekly_trend": weekly_trend,
                            "win_prob": None,
                            "theme_label": "",
                            "theme_score": None,
                            "overall_score": int(overall_score),
                            "size_mult": None,
                            "notes": {"tried": tried},
                            "slope_annual": float(slope_annual or 0.0),
                            "confidence": float(confidence or 0.5),
                            "window_days": int(days),
                        },
                    )
                self.stdout.write(self.style.SUCCESS(f"saved trend: {tkr} asof={asof}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"save error {tkr}: {e}"))

        self.stdout.write(self.style.SUCCESS("trend build done."))