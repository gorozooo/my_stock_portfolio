# =========================================================
# [FILE] tradeai_notify_all.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_notify_all.py
#
# このファイルは何？
# - tradeai の通知系コマンドをまとめて実行する管理コマンドです。
# - ウォッチ通知と保有通知を1回で順番に回せます。
# - cron からはこのコマンドを叩くだけでよくなります。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command


class Command(BaseCommand):
    help = "tradeai のウォッチ通知と保有通知をまとめて実行します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            default="",
            help="対象ユーザー名。省略時は settings.STOCKS_OWNER_USERNAME を使います。",
        )
        parser.add_argument(
            "--watch-max-items",
            type=int,
            default=3,
            help="ウォッチ通知で送る最大件数です。",
        )
        parser.add_argument(
            "--holding-max-items",
            type=int,
            default=3,
            help="保有通知で送る最大件数です。",
        )
        parser.add_argument(
            "--watch-dedupe-minutes",
            type=int,
            default=180,
            help="ウォッチ通知の重複抑制時間（分）です。",
        )
        parser.add_argument(
            "--holding-dedupe-minutes",
            type=int,
            default=180,
            help="保有通知の重複抑制時間（分）です。",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="送信せず、プレビューだけ実行します。",
        )

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        if not username:
            username = getattr(settings, "STOCKS_OWNER_USERNAME", "").strip()

        if not username:
            raise CommandError("対象ユーザー名がありません。--username を付けるか STOCKS_OWNER_USERNAME を設定してください。")

        watch_max_items = max(1, int(options.get("watch_max_items") or 3))
        holding_max_items = max(1, int(options.get("holding_max_items") or 3))
        watch_dedupe_minutes = max(1, int(options.get("watch_dedupe_minutes") or 180))
        holding_dedupe_minutes = max(1, int(options.get("holding_dedupe_minutes") or 180))
        dry_run = bool(options.get("dry_run"))

        failures: list[str] = []

        self.stdout.write(self.style.SUCCESS("=== tradeai 通知一括実行 開始 ==="))
        self.stdout.write(f"user                    : {username}")
        self.stdout.write(f"dry_run                 : {dry_run}")
        self.stdout.write("")

        self.stdout.write(self.style.SUCCESS("--- ウォッチ通知 ---"))
        try:
            call_command(
                "tradeai_notify_watch_signals",
                username=username,
                max_items=watch_max_items,
                dedupe_minutes=watch_dedupe_minutes,
                dry_run=dry_run,
            )
        except Exception as exc:
            failures.append(f"watch: {exc}")
            self.stdout.write(self.style.ERROR(f"[ERROR] watch 通知に失敗: {exc}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("--- 保有通知 ---"))
        try:
            call_command(
                "tradeai_notify_holdings",
                username=username,
                max_items=holding_max_items,
                dedupe_minutes=holding_dedupe_minutes,
                dry_run=dry_run,
            )
        except Exception as exc:
            failures.append(f"holdings: {exc}")
            self.stdout.write(self.style.ERROR(f"[ERROR] holdings 通知に失敗: {exc}"))

        self.stdout.write("")
        if failures:
            raise CommandError(" / ".join(failures))

        self.stdout.write(self.style.SUCCESS("=== tradeai 通知一括実行 完了 ==="))