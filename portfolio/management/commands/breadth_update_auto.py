# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from datetime import datetime, date
from typing import Any, Dict

from django.core.management.base import BaseCommand, CommandParser
from django.conf import settings


def _media_root() -> str:
    """MEDIA_ROOT が未設定でもCWDへフォールバック"""
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()


def _coerce_payload(opts: Dict[str, Any], d: str) -> Dict[str, Any]:
    """引数を安全に数値化してペイロード作成（負値はそのまま許容: 指数により起こりうる）"""
    def _i(key: str, default=0) -> int:
        try:
            return int(opts.get(key, default))
        except Exception:
            return int(default)

    def _f(key: str, default=0.0) -> float:
        try:
            return float(opts.get(key, default))
        except Exception:
            return float(default)

    return {
        "date": d,
        "adv": _i("adv", 0),
        "dec": _i("dec", 0),
        "up_vol": _f("up_vol", 0.0),
        "down_vol": _f("down_vol", 0.0),
        "new_high": _i("new_high", 0),
        "new_low": _i("new_low", 0),
        "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class Command(BaseCommand):
    help = "騰落・出来高ブレッドスを media/market/breadth_YYYY-MM-DD.json と breadth.json に保存する"

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
        # --- 日付の決定＆検証 ---
        d = (opts.get("date") or date.today().strftime("%Y-%m-%d")).strip()
        try:
            # YYYY-MM-DD バリデーション
            datetime.fromisoformat(d)
        except Exception:
            self.stdout.write(self.style.ERROR(f"invalid --date: {d}"))
            return

        # --- ペイロード作成 ---
        row = _coerce_payload(opts, d)

        # --- 保存先ディレクトリ ---
        mdir = os.path.join(_media_root(), "market")
        os.makedirs(mdir, exist_ok=True)

        dated_path = os.path.join(mdir, f"breadth_{d}.json")
        latest_path = os.path.join(mdir, "breadth.json")

        # --- 既存チェック（dated のみ尊重。latest は常に更新可） ---
        if os.path.exists(dated_path) and not opts.get("force"):
            self.stdout.write(self.style.WARNING(f"exists: {dated_path} (use --force to overwrite)"))
        else:
            with open(dated_path, "w", encoding="utf-8") as f:
                json.dump(row, f, ensure_ascii=False, indent=2)
            self.stdout.write(self.style.SUCCESS(f"wrote: {dated_path}"))

        # --- 最新ポインタも更新（常に上書き） ---
        try:
            with open(latest_path, "w", encoding="utf-8") as f:
                json.dump(row, f, ensure_ascii=False, indent=2)
            self.stdout.write(self.style.SUCCESS(f"updated latest: {latest_path}"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"failed to write latest: {e}"))