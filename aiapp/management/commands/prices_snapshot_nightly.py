# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import time
import pathlib
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices, SNAP_DIR


UNIVERSE_DIR = pathlib.Path("aiapp/data/universe")


def _load_universe(name: str) -> list[str]:
    if name.lower() in ("all", "jp-all", "jpall"):
        return list(StockMaster.objects.values_list("code", flat=True))
    path = UNIVERSE_DIR / f"{name}.txt"
    if not path.exists():
        raise CommandError(f"universe file not found: {path}")
    codes = [c.strip() for c in path.read_text().splitlines() if c.strip()]
    return codes


def _ensure_dir(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _save_csv(dirpath: pathlib.Path, code: str, df: pd.DataFrame) -> None:
    """
    get_prices() は columns=["Open","High","Low","Close","Volume"] を返す前提。
    CSV保存は小文字 open/high/low/close/volume に揃える。
    """
    out = dirpath / f"{code}.csv"
    dfx = df.copy()

    # 念のため列の存在チェック（壊れたデータはスキップできるように）
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in dfx.columns for c in required):
        return

    dfx = dfx[required].copy()
    dfx.columns = ["open", "high", "low", "close", "volume"]

    dfx.index.name = "Date"
    dfx.reset_index()[["Date", "open", "high", "low", "close", "volume"]].to_csv(
        out, index=False, quoting=csv.QUOTE_MINIMAL
    )


class Command(BaseCommand):
    help = "夜間に全銘柄のEODスナップショットをCSVで保存"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="all", help="all / nk225 / quick_100 / <file name>")
        parser.add_argument("--jobs", type=int, default=12)
        parser.add_argument("--nbars", type=int, default=800, help="保存本数の上限（古い方は落ちる）")

    def handle(self, *args, **opts):
        universe = opts["universe"]
        jobs = int(opts["jobs"])
        nbars = int(opts["nbars"])

        codes = _load_universe(universe)
        if not codes:
            self.stdout.write(self.style.WARNING("[snapshot] universe empty"))
            return

        day_dir = pathlib.Path(SNAP_DIR) / dt.date.today().strftime("%Y%m%d")
        _ensure_dir(day_dir)

        self.stdout.write(f"[snapshot] start universe={universe} codes={len(codes)} save={day_dir}")
        start = time.time()

        ok = 0
        with ThreadPoolExecutor(max_workers=max(4, jobs)) as ex:
            futs = {ex.submit(get_prices, c, nbars): c for c in codes}
            for fut in as_completed(futs):
                code = futs[fut]
                try:
                    df = fut.result(timeout=60)
                    if isinstance(df, pd.DataFrame) and (not df.empty):
                        _save_csv(day_dir, str(code), df)
                        ok += 1
                except Exception:
                    pass

        self.stdout.write(f"[snapshot] done ok={ok}/{len(codes)} dur={time.time()-start:.1f}s out={day_dir}")