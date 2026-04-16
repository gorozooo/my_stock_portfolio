# =========================================================
# [FILE] tradeai_capture_learning_snapshots.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_capture_learning_snapshots.py
#
# このファイルは何？
# - tradeai の監視結果を、学習用スナップショットとして保存する管理コマンドです。
# - ウォッチ監視も保有監視も、候補だけでなく全件を保存します。
# - 見送りも学習対象にするための入口です。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.services.holdings.monitor_service import build_holding_monitor_rows
from tradeai.services.learning.snapshot_writer import (
    append_holding_snapshots,
    append_watch_snapshots,
)
from tradeai.services.watchlist.monitor_service import build_watch_signal_rows


class Command(BaseCommand):
    help = "tradeai の監視結果を学習用スナップショットとして保存します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            default="",
            help="対象ユーザー名。省略時は settings.STOCKS_OWNER_USERNAME を使います。",
        )
        parser.add_argument(
            "--skip-watch",
            action="store_true",
            help="ウォッチ監視スナップショットを保存しません。",
        )
        parser.add_argument(
            "--skip-holdings",
            action="store_true",
            help="保有監視スナップショットを保存しません。",
        )

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        if not username:
            username = getattr(settings, "STOCKS_OWNER_USERNAME", "").strip()

        if not username:
            raise CommandError("対象ユーザー名がありません。--username を付けるか STOCKS_OWNER_USERNAME を設定してください。")

        User = get_user_model()

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(f"ユーザーが見つかりません: {username}") from exc

        skip_watch = bool(options.get("skip_watch"))
        skip_holdings = bool(options.get("skip_holdings"))

        self.stdout.write(self.style.SUCCESS("=== tradeai 学習用スナップショット保存 開始 ==="))
        self.stdout.write(f"user              : {username}")

        if not skip_watch:
            watch_rows = build_watch_signal_rows(user)
            watch_path, watch_count = append_watch_snapshots(user, watch_rows)
            self.stdout.write(f"watch_rows        : {len(watch_rows)}")
            self.stdout.write(f"watch_saved       : {watch_count}")
            self.stdout.write(f"watch_path        : {watch_path}")

        if not skip_holdings:
            holding_rows = build_holding_monitor_rows(user)
            holding_path, holding_count = append_holding_snapshots(user, holding_rows)
            self.stdout.write(f"holding_rows      : {len(holding_rows)}")
            self.stdout.write(f"holding_saved     : {holding_count}")
            self.stdout.write(f"holding_path      : {holding_path}")

        self.stdout.write(self.style.SUCCESS("=== tradeai 学習用スナップショット保存 完了 ==="))