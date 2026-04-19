# =========================================================
# [FILE] tradeai_backfill_learning_snapshots.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_backfill_learning_snapshots.py
#
# このファイルは何？
# - 既存デモ建玉に対して LearningSnapshot を後付け補完する管理コマンドです。
# - デフォルトでは OPEN 建玉だけを対象にします。
# - 必要なら --all-statuses で CLOSED / CANCELED も含められます。
# =========================================================

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.services.learning.backfill_service import backfill_learning_snapshots_for_user


class Command(BaseCommand):
    help = "既存デモ建玉に対して LearningSnapshot を後付け補完します。"

    def add_arguments(self, parser):
        parser.add_argument("--username", type=str, default="", help="対象ユーザー名")
        parser.add_argument(
            "--all-statuses",
            action="store_true",
            help="OPEN 以外も含めて補完する",
        )

    def handle(self, *args, **options):
        username = str(options.get("username") or "").strip()
        all_statuses = bool(options.get("all_statuses"))

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
            result = backfill_learning_snapshots_for_user(
                user=user,
                open_only=not all_statuses,
            )

            self.stdout.write("")
            self.stdout.write(f"user                  : {user.username}")
            self.stdout.write(f"target_trade_count    : {result['target_trade_count']}")
            self.stdout.write(f"created_count         : {result['created_count']}")
            self.stdout.write(f"skipped_existing_count: {result['skipped_existing_count']}")

            created_snapshots = list(result.get("created_snapshots") or [])
            if created_snapshots:
                self.stdout.write("created_snapshots:")
                for snapshot in created_snapshots:
                    self.stdout.write(
                        f"  - {snapshot.ticker} / {snapshot.direction} / "
                        f"{snapshot.source_scope} / score {snapshot.signal_score}"
                    )