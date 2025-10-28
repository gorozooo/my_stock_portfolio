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

try:
    from advisor.models_cache import PriceCache
except Exception:
    PriceCache = None  # type: ignore

from portfolio.models import Holding

User = get_user_model()
ALIAS_JSON = Path("media/advisor/symbol_alias.json")


# ---------- helpers ----------
def _load_alias_map() -> Dict[str, str]:
    try:
        if ALIAS_JSON.exists():
            with open(ALIAS_JSON, "r", encoding="utf-8") as f:
                m = json.load(f)
                return {str(k).upper(): str(v).upper() for k, v in m.items()}
    except Exception:
        pass
    return {}


def _symbol_candidates(raw: str, alias_map: Dict[str, str]) -> List[str]:
    """Yahoo! Finance向けの取得候補を複数生成（絶対スキップしない）"""
    t = (raw or "").strip().upper()
    if not t:
        return []
    cands = []
    if t in alias_map:
        cands.append(alias_map[t])
    cands.append(t)
    for suf in [".T", ".JP", ".TYO"]:
        if not t.endswith(suf):
            cands.append(f"{t}{suf}")
    if len(t) >= 2 and t[:-1].isdigit() and t[-1].isalpha():
        cands.append(f"{t[:-1]}.T")
    return list(dict.fromkeys(cands))


def _close_series(df: pd.DataFrame) -> Optional[pd.Series]:
    if df is None or df.empty or "Close" not in df.columns:
        return None
    close = df["Close"]
    if isinstance(close, pd.DataFrame) and close.shape[1] > 0:
        close = close.iloc[:, 0]
    try:
        return close.astype(float)
    except Exception:
        return None


def _weekly_trend_from_prices(df: pd.DataFrame) -> str:
    close = _close_series(df)
    if close is None or close.empty:
        return "flat"
    tail = close.tail(20)
    if len(tail) < 5:
        return "flat"
    y = tail.values
    x = np.arange(len(y), dtype=float)
    slope = ((x - x.mean()) * (y - y.mean())).sum() / ((x - x.mean()) ** 2).sum()
    return "up" if slope > 0 else ("down" if slope < 0 else "flat")


def _slope_annual_from_prices(df: pd.DataFrame) -> float:
    close = _close_series(df)
    if close is None or close.empty or len(close) < 5:
        return 0.0
    logret = np.log(close.clip(lower=1e-9)).diff().dropna()
    if logret.empty:
        return 0.0
    mu = float(logret.mean())
    return float(np.exp(mu * 250) - 1.0)


def _confidence_from_df(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.5
    n = len(df)
    base = 0.3 + min(0.6, n / 200.0)
    return float(round(max(0.3, min(0.9, base)), 3))


def _collect_targets(user) -> List[str]:
    s: Set[str] = set()
    for w in WatchEntry.objects.filter(user=user, status=WatchEntry.STATUS_ACTIVE).only("ticker"):
        if w.ticker:
            s.add(w.ticker.strip().upper())
    for h in Holding.objects.filter(user=user).only("ticker"):
        if h.ticker:
            s.add(h.ticker.strip().upper())
    return list(s)[:100]


def _fallback_price(tkr: str, user) -> Optional[int]:
    if PriceCache:
        pc = PriceCache.objects.filter(ticker=tkr.upper()).first()
        if pc and pc.last_price:
            try:
                return int(pc.last_price)
            except Exception:
                pass
    h = Holding.objects.filter(user=user, ticker=tkr.upper()).only("last_price").first()
    if h and h.last_price:
        try:
            return int(round(float(h.last_price)))
        except Exception:
            pass
    return None


# ---------- command ----------
class Command(BaseCommand):
    help = "Build TrendResult for holdings/watchlist with fallback and alias."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=60)
        parser.add_argument("--user-id", type=int, default=None)

    def handle(self, *args, **opts):
        days = int(opts.get("days") or 60)
        user_id = opts.get("user_id")
        user = User.objects.filter(id=user_id).first() if user_id else User.objects.first()
        if not user:
            self.stdout.write(self.style.ERROR("No user found"))
            return

        alias_map = _load_alias_map()
        targets = _collect_targets(user)
        self.stdout.write(f"[trend] days={days} targets={len(targets)}")

        for tkr in targets:
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
                    if df is not None and not df.empty:
                        break
                except Exception:
                    continue
                finally:
                    time.sleep(1)

            if df is None or df.empty:
                asof = date.today()
                weekly_trend = "flat"
                slope_annual = 0.0
                confidence = 0.5
                close_price = _fallback_price(tkr, user)
            else:
                df = df.tail(days).copy()
                idx = df.index[-1]
                asof = date(idx.year, idx.month, idx.day)
                weekly_trend = _weekly_trend_from_prices(df)
                slope_annual = _slope_annual_from_prices(df)
                confidence = _confidence_from_df(df)
                cs = _close_series(df)
                close_price = float(cs.iloc[-1]) if cs is not None and not cs.empty else _fallback_price(tkr, user)

            entry_price_hint = int(round(close_price)) if close_price else _fallback_price(tkr, user) or 3000

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
                            "slope_annual": slope_annual,
                            "confidence": confidence,
                            "window_days": days,
                            "notes": {"tried": tried},
                        },
                    )
                self.stdout.write(self.style.SUCCESS(f"saved trend: {tkr} asof={asof}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"save error {tkr}: {e}"))

        self.stdout.write(self.style.SUCCESS("trend build done."))