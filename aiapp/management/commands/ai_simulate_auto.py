# -*- coding: utf-8 -*-
"""
ai_simulate_auto

07:00 バッチ用のフル自動シミュレコマンド。
- picks_build が出力した media/aiapp/picks/latest_full.json を読み、
  その TopK 銘柄を「自動エントリー候補」として JSONL に書き出す。
- 現時点では DB には書き込まず、ファイルベースのログのみ残す。
  （将来、実エントリー/紙トレ/バックテスト用に拡張しやすい形）

出力:
- media/aiapp/sim/sim_orders_YYYYMMDD.jsonl
    1行1レコードの JSON Lines 形式
    {
      "ts": "...JST ISO8601...",
      "run_date": "YYYY-MM-DD",
      "code": "7203",
      "name": "トヨタ自動車",
      "sector": "輸送用機器",
      "entry": 1234.5,
      "tp": 1300.0,
      "sl": 1180.0,
      "last_close": 1220.0,
      "stars": 4,
      "score": 0.73,
      "qty_rakuten": 100,
      "qty_matsui": 100,
      "mode": "AUTO",
      "source": "ai_simulate_auto"
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from django.core.management.base import BaseCommand


PICKS_DIR = Path("media/aiapp/picks")
SIM_DIR = Path("media/aiapp/sim")


def dt_now_jst_iso() -> str:
    from datetime import datetime, timezone, timedelta

    JST = timezone(timedelta(hours=9))
    return datetime.now(JST).isoformat()


def today_jst_str() -> str:
    from datetime import datetime, timezone, timedelta

    JST = timezone(timedelta(hours=9))
    return datetime.now(JST).date().isoformat()


class Command(BaseCommand):
    help = "AI自動シミュレ用のAUTO注文スナップショットを保存する（07:00バッチ想定）"

    def add_arguments(self, parser):
        # 将来用に --date だけ受けられるようにしておく（デフォルトは今日）
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="シミュレーション日（YYYY-MM-DD）。指定がなければJSTの今日。",
        )

    def handle(self, *args, **options):
        run_date = options.get("date") or today_jst_str()
        picks_path = PICKS_DIR / "latest_full.json"

        if not picks_path.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"[ai_simulate_auto] picks file not found: {picks_path}"
                )
            )
            return

        try:
            raw = picks_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f"[ai_simulate_auto] failed to load picks json: {e}"
                )
            )
            return

        items: List[Dict[str, Any]] = data.get("items") or []

        if not items:
            self.stdout.write(
                self.style.WARNING(
                    "[ai_simulate_auto] items=0 (no picks to simulate)"
                )
            )
            return

        SIM_DIR.mkdir(parents=True, exist_ok=True)

        out_path = SIM_DIR / f"sim_orders_{run_date}.jsonl"

        # 1行ずつ append する（同じ日付で再実行しても追記される形）
        cnt = 0
        with out_path.open("a", encoding="utf-8") as fw:
            for it in items:
                code = it.get("code")
                if not code:
                    continue

                rec: Dict[str, Any] = {
                    "ts": dt_now_jst_iso(),
                    "run_date": run_date,
                    "code": code,
                    "name": it.get("name"),
                    "sector": it.get("sector_display"),
                    "entry": it.get("entry"),
                    "tp": it.get("tp"),
                    "sl": it.get("sl"),
                    "last_close": it.get("last_close"),
                    "stars": it.get("stars"),
                    "score": it.get("score"),
                    "qty_rakuten": it.get("qty_rakuten"),
                    "qty_matsui": it.get("qty_matsui"),
                    "required_cash_rakuten": it.get("required_cash_rakuten"),
                    "required_cash_matsui": it.get("required_cash_matsui"),
                    "est_pl_rakuten": it.get("est_pl_rakuten"),
                    "est_pl_matsui": it.get("est_pl_matsui"),
                    "est_loss_rakuten": it.get("est_loss_rakuten"),
                    "est_loss_matsui": it.get("est_loss_matsui"),
                    "mode": "AUTO",
                    "source": "ai_simulate_auto",
                }

                fw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                cnt += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"[ai_simulate_auto] saved {cnt} records to {out_path}"
            )
        )