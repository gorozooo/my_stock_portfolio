# aiapp/management/commands/ai_sim_eval.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

# ---- 新しく作ったサービスを使用 ----
from aiapp.services.sim_eval_service import eval_sim_record


class Command(BaseCommand):
    """
    シミュレログ (/media/aiapp/simulate/*.jsonl) を読み取り、
    sim_eval_service で計算した結果（win/lose/flat・PL・TP/SL 到達日）
    を rec に付与して書き戻す。

    使い方:
      python manage.py ai_sim_eval
      python manage.py ai_sim_eval --days 5
      python manage.py ai_sim_eval --force
    """

    help = "AIシミュレログに結果（PL / ラベル）を付与する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "AIAPP_SIM_HORIZON_DAYS", 5),
            help="評価に使う営業日数（何営業日後の値動きまでチェックするか）",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="既に eval_* が付与されていても再評価する",
        )

    def handle(self, *args, **options) -> None:
        horizon_days: int = options["days"]
        force: bool = options["force"]

        sim_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate"
        if not sim_dir.exists():
            self.stdout.write(
                self.style.WARNING(f"[ai_sim_eval] シミュレディレクトリが存在しません: {sim_dir}")
            )
            return

        self.stdout.write(
            f"[ai_sim_eval] dir={sim_dir} horizon_days={horizon_days} force={force}"
        )

        total = 0
        evaluated = 0

        # ===== 全 JSONL ファイルを処理 =====
        for path in sorted(sim_dir.glob("*.jsonl")):
            self.stdout.write(f"  読み込み中: {path.name}")

            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"    読み込み失敗: {e}"))
                continue

            lines = text.splitlines()
            new_lines: List[str] = []

            for line in lines:
                raw = line.strip()
                if not raw:
                    continue

                try:
                    rec = json.loads(raw)
                except Exception:
                    # 壊れた行はそのまま残す
                    new_lines.append(raw)
                    continue

                total += 1

                # ===== 既存評価がある場合のスキップ判定 =====
                already_has_eval = (
                    "eval_label_rakuten" in rec or
                    "eval_label_matsui" in rec or
                    "eval_close_px" in rec
                )

                if already_has_eval and not force:
                    new_lines.append(json.dumps(rec, ensure_ascii=False))
                    continue

                # ===== sim_eval_service に評価させる =====
                try:
                    updated = eval_sim_record(rec, horizon_days=horizon_days)
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"    評価中にエラー: {e}"))
                    updated = rec  # 壊さないために rec をそのまま使用

                # updated は rec に eval_ 系が付いた dict
                new_lines.append(json.dumps(updated, ensure_ascii=False))
                evaluated += 1

            # ===== バックアップ作成 & 上書き =====
            backup = path.with_suffix(path.suffix + ".bak")

            try:
                if backup.exists():
                    backup.unlink()

                path.replace(backup)  # 現ファイル → backup
                path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  → 書き込み完了: {path.name} (backup: {backup.name})"
                    )
                )
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  書き込み失敗: {e}"))
                # 書き込み失敗時はバックアップを復旧
                if backup.exists() and not path.exists():
                    backup.replace(path)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"[ai_sim_eval] 全レコード: {total}件 / 評価したレコード: {evaluated}件"
            )
        )
        self.stdout.write(self.style.SUCCESS("[ai_sim_eval] 完了"))