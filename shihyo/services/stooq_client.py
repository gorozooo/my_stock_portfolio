"""
[FILE] stooq_client.py
[PATH] <project_root>/shihyo/services/stooq_client.py

このファイルは何？
- Stooq のCSVエンドポイントから価格データを取得する「取得部品」です。
- StooqのCSV URL形式: https://stooq.com/q/l/?s=%1&f=sd2t2ohlcv&h&e=csv を利用します。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Dict, Optional

import requests


@dataclass
class Quote:
    symbol: str
    date: str
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class StooqClient:
    BASE_URL = "https://stooq.com/q/l/"

    def fetch_latest(self, symbol: str, timeout_sec: int = 10) -> Quote:
        """
        symbol例:
          - ny.f (Nikkei 225 Futures on stooq)
          - usdjpy (USDJPY)
          - vi.c (VIX)
        """
        params = {
            "s": symbol,
            "f": "sd2t2ohlcv",  # Symbol, Date, Time, Open, High, Low, Close, Volume
            "h": "",            # header on
            "e": "csv",
        }
        r = requests.get(self.BASE_URL, params=params, timeout=timeout_sec)
        r.raise_for_status()

        # CSV parse
        text = r.text.strip()
        f = io.StringIO(text)
        reader = csv.DictReader(f)

        rows = list(reader)
        if not rows:
            raise RuntimeError(f"Empty CSV for symbol={symbol}")

        # Stooq returns single-line latest quote (typically)
        row = rows[-1]

        def _to_float(x: Optional[str]) -> float:
            if x is None:
                return float("nan")
            x = x.strip()
            if x == "" or x.lower() == "na":
                return float("nan")
            return float(x)

        return Quote(
            symbol=row.get("Symbol", symbol),
            date=row.get("Date", ""),
            time=row.get("Time", ""),
            open=_to_float(row.get("Open")),
            high=_to_float(row.get("High")),
            low=_to_float(row.get("Low")),
            close=_to_float(row.get("Close")),
            volume=_to_float(row.get("Volume")),
        )

    @staticmethod
    def calc_change(close: float, open_: float) -> Dict[str, float]:
        """
        Stooqのデータは「前日終値」が直接取れないケースがあるので、
        まずは “今日のOpen→Close” の変化を簡易差分として使います。
        （本番で前日終値ベースにしたくなったら拡張できます）
        """
        if open_ == 0 or open_ != open_ or close != close:  # nan check
            return {"change": 0.0, "pct": 0.0}
        change = close - open_
        pct = (change / open_) * 100.0
        return {"change": float(change), "pct": float(pct)}