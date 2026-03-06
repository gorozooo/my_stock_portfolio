"""
[FILE] stooq_client.py
[PATH] <project_root>/shihyo/services/stooq_client.py

このファイルは何？
- Stooq のCSVエンドポイントから価格データを取得する「取得部品」です。
- StooqのCSV URL形式: https://stooq.com/q/l/?s=%1&f=sd2t2ohlcv&h&e=csv を利用します。

今回の修正ポイント：
- Stooq が返す "N/D" を安全に処理する
- 空文字 / NA / N/D / None を float("nan") にする
- calc_change() でも欠損値に強くする
"""

from __future__ import annotations

import csv
import io
import math
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
          - ny.f   (Nikkei 225 Futures on stooq)
          - nkx    (Nikkei 225 spot on stooq)
          - usdjpy (USDJPY)
          - vi.c   (VIX)
        """
        params = {
            "s": symbol,
            "f": "sd2t2ohlcv",  # Symbol, Date, Time, Open, High, Low, Close, Volume
            "h": "",            # header on
            "e": "csv",
        }
        r = requests.get(self.BASE_URL, params=params, timeout=timeout_sec)
        r.raise_for_status()

        text = r.text.strip()
        f = io.StringIO(text)
        reader = csv.DictReader(f)

        rows = list(reader)
        if not rows:
            raise RuntimeError(f"Empty CSV for symbol={symbol}")

        row = rows[-1]

        def _to_float(x: Optional[str]) -> float:
            if x is None:
                return float("nan")

            x = x.strip()
            if x == "":
                return float("nan")

            upper = x.upper()
            if upper in {"NA", "N/A", "N/D", "ND", "-"}:
                return float("nan")

            try:
                return float(x)
            except (TypeError, ValueError):
                return float("nan")

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
        Stooqのデータは前日終値が直接取れないケースがあるため、
        まずは “当日Open→Close” の差を簡易変化率として使う。

        欠損値（nan）や open=0 の場合は安全に 0 を返す。
        """
        if (
            open_ == 0
            or math.isnan(open_)
            or math.isnan(close)
            or math.isinf(open_)
            or math.isinf(close)
        ):
            return {"change": 0.0, "pct": 0.0}

        change = close - open_
        pct = (change / open_) * 100.0
        return {"change": float(change), "pct": float(pct)}