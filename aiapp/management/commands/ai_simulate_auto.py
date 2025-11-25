# -*- coding: utf-8 -*-
"""
ai_simulate_auto

07:00 バッチ用の「フル自動シミュレ（紙トレ自動エントリー）」コマンド。

役割:
- 04:30 の picks_build が出力した media/aiapp/picks/latest_full.json を読み込む
- TopK 銘柄すべてに対して「AUTOエントリー注文（紙トレ）」を起票する
- 結果を JSON Lines 形式で media/aiapp/sim/sim_orders_YYYYMMDD.jsonl に保存
  → 16:00 の ai_sim_eval がこのファイルを使って勝敗評価する想定

形式（1行1レコード）:
{
  "run_id": "20251125_070000_auto",
  "ts": "2025-11-25T07:00:01+09:00",
  "run_date": "2025-11-25",
  "code": "7203",
  "name": "トヨタ自動車",
  "sector": "輸送用機器",
  "side": "BUY",
  "entry": 1234.5,
  "tp": 1300.0,
  "sl": 1180.0,
  "last_close": 1220.0,
  "stars": 4,
  "score": 0.73,
  "score_100": 73,
  "qty_rakuten": 100,
  "qty_matsui": 100,
  "required_cash_rakuten": 123450.0,
  "required_cash_matsui": 123450.0,
  "est_pl_rakuten": 6500.0,
  "est_pl_matsui": 6500.0,
  "est_loss_rakuten": -4500.0,
  "est_loss_matsui": -4500.0,
  "style": "aggressive",
  "horizon": "short",
  "universe": "all_jpx",
  "topk": 10,
  "mode": "AUTO",
  "source": "ai_simulate_auto"
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.core.management.base import BaseCommand


PICKS_DIR = Path("media/aiapp/picks")
SIM_DIR = Path("media/aiapp/sim")


# ========= 時刻ユーティリティ（JST固定） =========

def _now_jst():
    from datetime import datetime, timezone, timedelta

    JST = timezone(timedelta(hours=9))
    return datetime.now(JST)


def dt_now_jst_iso() -> str:
    return _now_jst().isoformat()


def today_jst_str() -> str:
    return _now_jst().date().isoformat()


def dt_now_run_id(prefix: str = "auto") -> str:
    """
    run_id 用のタイムスタンプ文字列。
    例: 20251125_070001_auto
    """
    n = _now_jst()
    return n.strftime("%Y%m%d_%H%M%S") + f"_{prefix}"


# ========= コマンド本体 =========

class Command(BaseCommand):
    help = "AIフル自動シミュレ用：AUTO紙トレ注文を JSONL に起票（07:00バッチ想定）"

    def add_arguments(self, parser):
        # 将来用：任意の日付で再生成したいときに --date を使う
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="シミュレーション日（YYYY-MM-DD）。指定がなければ JST の今日。",
        )
        # 将来用：既存ファイルを上書きしたい場合
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="同じ日付の sim_orders_YYYYMMDD.jsonl を上書きする（通常は追記）。",
        )

    def handle(self, *args, **options):
        run_date = options.get("date") or today_jst_str()
        overwrite: bool = bool(options.get("overwrite"))

        picks_path = PICKS_DIR / "latest_full.json"
        if not picks_path.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"[ai_simulate_auto] picks file not found: {picks_path}"
                )
            )
            return

        # -------- picks 読み込み --------
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

        meta: Dict[str, Any] = data.get("meta") or {}
        items: List[Dict[str, Any]] = data.get("items") or []

        if not items:
            self.stdout.write(
                self.style.WARNING(
                    "[ai_simulate_auto] items=0 (no picks to simulate)"
                )
            )
            return

        style = (meta.get("style") or "aggressive")
        horizon = (meta.get("horizon") or "short")
        universe = (meta.get("universe") or "unknown")
        topk = meta.get("topk")

        run_id = dt_now_run_id(prefix="auto")

        SIM_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SIM_DIR / f"sim_orders_{run_date}.jsonl"

        # 上書き or 追記
        mode = "w" if overwrite else "a"

        written = 0
        with out_path.open(mode, encoding="utf-8") as fw:
            for it in items:
                code = it.get("code")
                if not code:
                    continue

                # picks_build 側のフィールドをそのまま利用
                name = it.get("name")
                sector = it.get("sector_display")

                # 方向は現状「買い」前提
                side = "BUY"

                entry = it.get("entry", it.get("last_close"))
                tp = it.get("tp")
                sl = it.get("sl")
                last_close = it.get("last_close")

                score = it.get("score")
                score_100 = it.get("score_100")
                stars = it.get("stars")

                qty_rakuten = it.get("qty_rakuten")
                qty_matsui = it.get("qty_matsui")
                required_cash_rakuten = it.get("required_cash_rakuten")
                required_cash_matsui = it.get("required_cash_matsui")
                est_pl_rakuten = it.get("est_pl_rakuten")
                est_pl_matsui = it.get("est_pl_matsui")
                est_loss_rakuten = it.get("est_loss_raketen") if False else it.get("est_loss_rakuten")
                est_loss_matsui = it.get("est_loss_matsui")

                rec: Dict[str, Any] = {
                    "run_id": run_id,
                    "ts": dt_now_jst_iso(),
                    "run_date": run_date,
                    "code": code,
                    "name": name,
                    "sector": sector,
                    "side": side,
                    "entry": entry,
                    "tp": tp,
                    "sl": sl,
                    "last_close": last_close,
                    "stars": stars,
                    "score": score,
                    "score_100": score_100,
                    "qty_rakuten": qty_rakuten,
                    "qty_matsui": qty_matsui,
                    "required_cash_rakuten": required_cash_rakuten,
                    "required_cash_matsui": required_cash_matsui,
                    "est_pl_rakuten": est_pl_rakuten,
                    "est_pl_matsui": est_pl_matsui,
                    "est_loss_rakuten": est_loss_rakuten,
                    "est_loss_matsui": est_loss_matsui,
                    "style": style,
                    "horizon": horizon,
                    "universe": universe,
                    "topk": topk,
                    "mode": "AUTO",
                    "source": "ai_simulate_auto",
                }

                fw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"[ai_simulate_auto] run_id={run_id} date={run_date} "
                f"written={written} -> {out_path}"
            )
        )