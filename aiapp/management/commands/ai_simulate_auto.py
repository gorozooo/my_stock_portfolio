# -*- coding: utf-8 -*-
"""
ai_simulate_auto

07:00 バッチ用の「フル自動シミュレ（紙トレ自動エントリー）」コマンド。

役割:
- picks_build が出力した media/aiapp/picks/latest_full.json を読み込む
- TopK 銘柄すべてに対して「DEMOモードの紙トレ注文」を JSONL に起票する
- 出力先は /media/aiapp/simulate/sim_orders_YYYY-MM-DD.jsonl
  → レベル3判定・行動データセット・行動モデルが読む前提のフォーマット

1行の例:
{
  "user_id": 1,
  "mode": "demo",
  "ts": "2025-11-25T07:00:01+09:00",
  "run_date": "2025-11-25",
  "trade_date": "2025-11-25",
  "run_id": "20251125_070001_auto_demo",
  "code": "7203",
  "name": "トヨタ自動車",
  "sector": "輸送用機器",
  "side": "BUY",
  "entry": 1234.5,
  "tp": 1300.0,
  "sl": 1180.0,
  "last_close": 1220.0,
  "qty_rakuten": 100,
  "qty_matsui": 100,
  "est_pl_rakuten": 6500.0,
  "est_pl_matsui": 6500.0,
  "est_loss_rakuten": -4500.0,
  "est_loss_matsui": -4500.0,
  "required_cash_rakuten": 123450.0,
  "required_cash_matsui": 123450.0,
  "score": 0.73,
  "score_100": 73,
  "stars": 4,
  "style": "aggressive",
  "horizon": "short",
  "universe": "all_jpx",
  "topk": 10,
  "source": "ai_simulate_auto"
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


# ========= パス定義（MEDIA_ROOT ベース） =========

PICKS_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "picks"
SIM_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"


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
    help = "AIフル自動シミュレ用：DEMO紙トレ注文を JSONL に起票（07:00バッチ想定）"

    def add_arguments(self, parser):
        # 任意の日付で再生成したいとき用（デフォルトはJSTでの今日）
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="シミュレーション日（YYYY-MM-DD）。指定がなければJSTの今日。",
        )
        # 既存ファイルを上書きしたい場合に使う（通常は追記でOK）
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="同じ日付の jsonl を上書きして作り直す（通常は追記）。",
        )

    def handle(self, *args, **options):
        # run_date: 「このシミュレが有効になる営業日」のベース
        # 07:00 バッチでは run_date = 今日（その日の寄り〜大引け分を想定）
        run_date = options.get("date") or today_jst_str()
        overwrite: bool = bool(options.get("overwrite"))

        # trade_date:
        #   ai_simulate_auto の世界では「run_date = trade_date」として扱う。
        #   （15:00以降や寄り前の扱いは、run_date の指定側でコントロールする）
        trade_date = run_date

        # -------- picks 読み込み --------
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

        meta: Dict[str, Any] = data.get("meta") or {}
        items: List[Dict[str, Any]] = data.get("items") or []

        if not items:
            self.stdout.write(
                self.style.WARNING(
                    "[ai_simulate_auto] items=0 (no picks to simulate)"
                )
            )
            return

        # -------- ユーザー決定（単ユーザー前提で最初のユーザーを使う） --------
        User = get_user_model()
        user = User.objects.order_by("id").first()
        if not user:
            self.stdout.write(
                self.style.ERROR("[ai_simulate_auto] no user found")
            )
            return
        user_id = user.id

        # meta からスタイル情報を継承
        style = (meta.get("style") or "aggressive")
        horizon = (meta.get("horizon") or "short")
        universe = (meta.get("universe") or "unknown")
        topk = meta.get("topk")

        run_id = dt_now_run_id(prefix="auto_demo")

        SIM_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SIM_DIR / f"sim_orders_{run_date}.jsonl"

        mode = "w" if overwrite else "a"

        written = 0
        with out_path.open(mode, encoding="utf-8") as fw:
            for it in items:
                code = it.get("code")
                if not code:
                    continue

                name = it.get("name")
                sector = it.get("sector_display")

                # DEMO紙トレなので side=BUY / mode=demo 固定
                side = "BUY"
                rec_mode = "demo"

                entry = it.get("entry", it.get("last_close"))
                tp = it.get("tp")
                sl = it.get("sl")
                last_close = it.get("last_close")

                qty_rakuten = it.get("qty_rakuten")
                qty_matsui = it.get("qty_matsui")
                est_pl_rakuten = it.get("est_pl_rakuten")
                est_pl_matsui = it.get("est_pl_matsui")
                est_loss_rakuten = it.get("est_loss_rakuten")
                est_loss_matsui = it.get("est_loss_matsui")
                required_cash_rakuten = it.get("required_cash_rakuten")
                required_cash_matsui = it.get("required_cash_matsui")

                score = it.get("score")
                score_100 = it.get("score_100")
                stars = it.get("stars")

                rec: Dict[str, Any] = {
                    # レベル3評価・行動分析が見るキー
                    "user_id": user_id,
                    "mode": rec_mode,               # "all"/"live"/"demo" フィルタ用
                    "ts": dt_now_jst_iso(),         # 実際にシミュレを起票した時刻
                    "run_date": run_date,           # バッチの対象日
                    "trade_date": trade_date,       # 「指値が有効になる日」（5分足評価もこの日付ベース）
                    "run_id": run_id,               # 同一バッチの識別子
                    # 銘柄情報
                    "code": code,
                    "name": name,
                    "sector": sector,
                    "side": side,
                    # エントリー条件
                    "entry": entry,
                    "tp": tp,
                    "sl": sl,
                    "last_close": last_close,
                    # 数量・損益想定
                    "qty_rakuten": qty_rakuten,
                    "qty_matsui": qty_matsui,
                    "est_pl_rakuten": est_pl_rakuten,
                    "est_pl_matsui": est_pl_matsui,
                    "est_loss_rakuten": est_loss_rakuten,
                    "est_loss_matsui": est_loss_matsui,
                    "required_cash_rakuten": required_cash_rakuten,
                    "required_cash_matsui": required_cash_matsui,
                    # スコア情報
                    "score": score,
                    "score_100": score_100,
                    "stars": stars,
                    # picks のメタ
                    "style": style,
                    "horizon": horizon,
                    "universe": universe,
                    "topk": topk,
                    "source": "ai_simulate_auto",
                }

                fw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"[ai_simulate_auto] run_id={run_id} date={run_date} "
                f"user_id={user_id} written={written} -> {out_path}"
            )
        )