# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from datetime import datetime, date
from django.core.management.base import BaseCommand, CommandParser
from django.conf import settings

def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

class Command(BaseCommand):
    help = "騰落・出来高ブレッドスを media/market/breadth_YYYY-MM-DD.json に保存する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--date", type=str, default="", help="対象日(YYYY-MM-DD)。未指定で今日")
        parser.add_argument("--adv", type=int, default=0, help="上昇数 (advance)")
        parser.add_argument("--dec", type=int, default=0, help="下落数 (decline)")
        parser.add_argument("--up_vol", type=float, default=0.0, help="上昇銘柄の出来高合計")
        parser.add_argument("--down_vol", type=float, default=0.0, help="下落銘柄の出来高合計")
        parser.add_argument("--new_high", type=int, default=0, help="新高値銘柄数")
        parser.add_argument("--new_low", type=int, default=0, help="新安値銘柄数")
        parser.add_argument("--force", action="store_true", help="同日の既存ファイルがあっても上書き")

    def handle(self, *args, **opts):
        d = opts["date"] or date.today().strftime("%Y-%m-%d")
        try:
            datetime.fromisoformat(d)
        except Exception:
            return self.stdout.write(self.style.ERROR(f"invalid --date: {d}"))

        row = {
            "date": d,
            "adv": int(opts["adv"]),
            "dec": int(opts["dec"]),
            "up_vol": float(opts["up_vol"]),
            "down_vol": float(opts["down_vol"]),
            "new_high": int(opts["new_high"]),
            "new_low": int(opts["new_low"]),
        }
        mdir = os.path.join(_media_root(), "market")
        os.makedirs(mdir, exist_ok=True)
        path = os.path.join(mdir, f"breadth_{d}.json")
        if os.path.exists(path) and not opts["force"]:
            return self.stdout.write(self.style.WARNING(f"exists: {path} (use --force to overwrite)"))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(row, f, ensure_ascii=False, indent=2)
        self.stdout.write(self.style.SUCCESS(f"wrote: {path}"))