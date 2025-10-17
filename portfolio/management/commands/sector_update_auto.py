# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional
import math
import time

from django.core.management.base import BaseCommand, CommandParser
from django.conf import settings

from ...models_market import SectorSignal

# 任意依存。未導入でもコマンドは安全終了する。
try:
    import yfinance as yf
    import pandas as pd
except Exception:
    yf = None
    pd = None


def _calc_base_close(close: "pd.Series") -> Tuple[float, float, float]:
    """5日/20日騰落の合成（素点）。"""
    if close is None or len(close) < 21:
        return 0.0, 0.0, 0.0
    try:
        chg5 = (close.iloc[-1] / close.iloc[-6] - 1.0) * 100.0
    except Exception:
        chg5 = 0.0
    try:
        chg20 = (close.iloc[-1] / close.iloc[-21] - 1.0) * 100.0
    except Exception:
        chg20 = 0.0
    base = 0.6 * chg5 + 0.4 * chg20
    return float(base), float(chg5), float(chg20)


def _vol_ratio(vol: "pd.Series") -> Optional[float]:
    """出来高比: 近20/過去60 の比."""
    if vol is None or len(vol) < 80:
        return None
    v20 = float(vol.iloc[-20:].mean() or 0.0)
    v60 = float(vol.iloc[-80:-20].mean() or 0.0)
    if v60 <= 0:
        return None
    return float(v20 / v60)


def _tanh_standardize(values: List[float]) -> List[float]:
    """同日セクター間の相対化（z-score→tanhで-1..+1へ圧縮）。"""
    if not values:
        return []
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / max(len(values), 1)
    sd = math.sqrt(var) if var > 0 else 1.0
    z = [(v - m) / (sd or 1.0) for v in values]
    return [math.tanh(v) for v in z]


def _chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]


class Command(BaseCommand):
    help = "セクター強弱を自動取得して SectorSignal に保存（yfinance利用）。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--for-date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定は今日")
        parser.add_argument("--days", type=int, default=None, help="lookback日数（settings未指定時に上書き）")
        parser.add_argument("--backfill", type=int, default=0, help="過去N日ぶん前方埋め（営業日想定で日次）")
        parser.add_argument("--skip-if-exists", action="store_true", help="同日の同セクターが既にあればスキップ")
        parser.add_argument("--chunk", type=int, default=24, help="yfinanceの同時取得チャンク幅（多すぎると失敗しやすい）")
        parser.add_argument("--sleep", type=float, default=0.8, help="チャンク間スリープ秒（レート制限対策）")

    def handle(self, *args, **opts):
        if yf is None or pd is None:
            self.stdout.write(self.style.WARNING(
                "yfinance/pandas が見つかりません。`pip install yfinance pandas` を実行してください。"
            ))
            return

        symmap: Dict[str, str] = getattr(settings, "ADVISOR_SECTOR_SYMBOLS", {})
        if not symmap:
            self.stdout.write(self.style.WARNING("settings.ADVISOR_SECTOR_SYMBOLS が未設定です。"))
            return

        lookback = int(opts["days"] or getattr(settings, "ADVISOR_SECTOR_LOOKBACK_DAYS", 90))
        base_day: date
        if opts["for_date"]:
            yyyy, mm, dd = [int(x) for x in opts["for_date"].split("-")]
            base_day = date(yyyy, mm, dd)
        else:
            base_day = date.today()

        # backfill: base_day, base_day-1, ...
        days_list = [base_day - timedelta(days=i) for i in range(int(opts["backfill"] or 0) + 1)]

        all_symbols = list(symmap.values())
        chunk_size = max(1, int(opts["chunk"]))
        sleep_sec = max(0.0, float(opts["sleep"]))

        for the_day in days_list:
            start = the_day - timedelta(days=lookback + 5)
            end = the_day + timedelta(days=1)

            self.stdout.write(f"[sector_update_auto] {the_day} fetching {len(all_symbols)} syms ({start} → {end})")
            raws: List[Tuple[str, float, float, float, Optional[float]]] = []

            # チャンク分割 & リトライ
            for part in _chunk(all_symbols, chunk_size):
                for attempt in range(3):
                    try:
                        data = yf.download(
                            part, start=start.isoformat(), end=end.isoformat(),
                            progress=False, group_by="ticker", auto_adjust=False
                        )
                        # partごとに集計
                        for sector, sym in symmap.items():
                            if sym not in part:
                                continue
                            try:
                                df = data[sym] if isinstance(data.columns, pd.MultiIndex) else data
                                close = df["Close"].dropna()
                                vol = df["Volume"].dropna()
                                base, chg5, chg20 = _calc_base_close(close)
                                vr = _vol_ratio(vol)
                                raws.append((sector, base, chg5, chg20, vr))
                            except Exception:
                                raws.append((sector, 0.0, 0.0, 0.0, None))
                        break  # 成功
                    except Exception as e:
                        if attempt == 2:
                            self.stdout.write(self.style.WARNING(f"   fetch failed for chunk {part}: {e}"))
                        time.sleep(0.8 * (attempt + 1))
                time.sleep(sleep_sec)

            # 正規化（同日セクター間）
            bases = [r[1] for r in raws] if raws else []
            norms = _tanh_standardize(bases) if bases else []

            created = updated = 0
            for (sector, base, chg5, chg20, vr), score in zip(raws, norms):
                if opts["skip_if_exists"] and SectorSignal.objects.filter(date=the_day, sector=sector).exists():
                    continue
                obj, is_new = SectorSignal.objects.update_or_create(
                    date=the_day, sector=sector,
                    defaults=dict(
                        rs_score=float(score),
                        advdec=None,
                        vol_ratio=(None if vr is None else float(vr)),
                        meta=dict(chg5=float(chg5), chg20=float(chg20), base=float(base)),
                    )
                )
                created += 1 if is_new else 0
                updated += 0 if is_new else 1

            self.stdout.write(self.style.SUCCESS(
                f"[sector_update_auto] {the_day} upsert done. created={created}, updated={updated}"
            ))