# portfolio/management/commands/update_last_prices.py
from __future__ import annotations
import sys
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from django.core.management.base import BaseCommand
from django.db import transaction

from portfolio.models import Holding
from portfolio.services import trend as svc_trend  # 保有ページと同じ正規化

JST = timezone(timedelta(hours=9))

def _norm(t: str) -> str:
    return svc_trend._normalize_ticker(str(t or ""))

def _download_last_close(norm_syms: List[str]) -> Dict[str, float]:
    if not norm_syms:
        return {}
    try:
        df = yf.download(
            tickers=norm_syms if len(norm_syms) > 1 else norm_syms[0],
            period="40d", interval="1d",
            auto_adjust=True, progress=False, group_by="ticker"
        )
    except Exception:
        df = None

    out: Dict[str, float] = {}
    def _pick_one(sym: str) -> Optional[float]:
        if df is None: return None
        try:
            if isinstance(df.columns, pd.MultiIndex):
                s = df[(sym, "Close")] if (sym, "Close") in df.columns else df.xs(sym, axis=1)["Close"]  # type: ignore[index]
            else:
                s = df["Close"]  # type: ignore[index]
        except Exception:
            return None
        try:
            v = float(pd.Series(s).dropna().iloc[-1])  # type: ignore[arg-type]
            return v if v > 0 else None
        except Exception:
            return None

    for s in norm_syms:
        v = _pick_one(s)
        if v is not None:
            out[s] = v
    return out

class Command(BaseCommand):
    help = "Update Holding.last_price with last close (yfinance)."

    def add_arguments(self, parser):
        parser.add_argument("--since-hours", type=int, default=12,
                            help="Skip if last_price_updated within this hours.")

    def handle(self, *args, **opts):
        since_hours = int(opts["since_hours"])
        cutoff = datetime.now(JST) - timedelta(hours=since_hours)

        qs = Holding.objects.all()
        # 直近更新済みはスキップしてAPI節約
        targets = [h for h in qs if not h.last_price_updated or h.last_price_updated < cutoff]
        if not targets:
            self.stdout.write(self.style.SUCCESS("No holdings to update."))
            return

        # 正規化まとめて取得
        mapping = {h.id: _norm(h.ticker) for h in targets if h.ticker}
        norm_syms = sorted({v for v in mapping.values() if v})
        price_map = _download_last_close(norm_syms)

        now = datetime.now(JST)
        changed: List[Holding] = []
        for h in targets:
            nsym = mapping.get(h.id)
            if not nsym: 
                continue
            v = price_map.get(nsym)
            if v is None: 
                continue
            try:
                h.last_price = v
                h.last_price_updated = now
                changed.append(h)
            except Exception:
                continue

        if not changed:
            self.stdout.write(self.style.WARNING("No prices fetched."))
            return

        with transaction.atomic():
            Holding.objects.bulk_update(changed, ["last_price", "last_price_updated"])

        self.stdout.write(self.style.SUCCESS(f"Updated {len(changed)} holdings."))