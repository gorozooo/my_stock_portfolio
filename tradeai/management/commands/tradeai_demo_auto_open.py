# =========================================================
# [FILE] tradeai_demo_auto_open.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_demo_auto_open.py
#
# このファイルは何？
# - 候補抽出からデモ建玉を自動でOPENする管理コマンドです。
# - 毎朝などに実行する想定です。
#
# 今回の修正：
# - LearningResult 件数と、
#   学習バイアス反映後の点数も出力するようにしています。
# =========================================================

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.services.demo.auto_entry_service import auto_open_demo_trades_for_user


class Command(BaseCommand):
    help = "候補抽出からデモ建玉を自動OPENします。"

    def add_arguments(self, parser):
        parser.add_argument("--username", type=str, default="", help="対象ユーザー名")
        parser.add_argument("--per-side", type=int, default=3, help="片側あたりのOPEN件数")
        parser.add_argument("--qty", type=int, default=100, help="1建玉あたりの数量")

    def handle(self, *args, **options):
        username = str(options.get("username") or "").strip()
        per_side = int(options.get("per_side") or 3)
        qty = int(options.get("qty") or 100)

        User = get_user_model()

        if username:
            try:
                users = [User.objects.get(username=username)]
            except User.DoesNotExist as exc:
                raise CommandError(f"ユーザーが見つかりません: {username}") from exc
        else:
            users = list(User.objects.all().order_by("id"))

        if not users:
            raise CommandError("対象ユーザーがいません。")

        for user in users:
            result = auto_open_demo_trades_for_user(
                user=user,
                per_side=per_side,
                qty=qty,
            )

            self.stdout.write("")
            self.stdout.write(f"user                   : {user.username}")
            self.stdout.write(f"candidate_total        : {result['candidate_total']}")
            self.stdout.write(f"learning_result_count  : {result['learning_result_count']}")
            self.stdout.write(f"learning_min_count     : {result['learning_min_count']}")
            self.stdout.write(f"created_count          : {result['created_count']}")
            self.stdout.write(f"skipped_existing       : {result['skipped_existing_count']}")
            self.stdout.write(f"skipped_missing_plan   : {result['skipped_missing_plan_count']}")

            created_trades = list(result.get("created_trades") or [])
            if created_trades:
                self.stdout.write("created_trades:")
                for trade in created_trades:
                    payload = trade.entry_payload or {}
                    base_score = payload.get("score_100")
                    adjusted_score = payload.get("adjusted_score_100")
                    learning_bias = payload.get("learning_bias_total")
                    learning_notes = list(payload.get("learning_bias_notes") or [])

                    score_text = ""
                    if base_score is not None and adjusted_score is not None:
                        score_text = f" / Score {base_score}→{adjusted_score}"
                    if learning_bias not in (None, 0):
                        score_text += f" / Bias {learning_bias:+d}"

                    self.stdout.write(
                        f"  - {trade.ticker} / {trade.direction} / "
                        f"Entry {trade.entry_price} / TP {trade.take_profit_price} / SL {trade.stop_price}"
                        f"{score_text}"
                    )

                    if learning_notes:
                        for note in learning_notes:
                            self.stdout.write(f"      * {note}")