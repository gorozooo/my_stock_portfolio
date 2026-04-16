# =========================================================
# [FILE] tradeai_notify_watch_signals.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_notify_watch_signals.py
#
# このファイルは何？
# - ウォッチ監視の結果から、LINE通知を送るコマンドです。
# - 同じ内容が短時間に何度も送られないよう、重複抑制も行います。
# - 送信対象は「emit_event=True」のものだけです。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.services.notify.dedupe_store import (
    cleanup_old_entries,
    mark_sent,
    was_sent_recently,
)
from tradeai.services.notify.line_push_service import send_line_text
from tradeai.services.watchlist.monitor_service import build_watch_signal_rows
from tradeai.services.watchlist.notify_builder import (
    build_watch_notify_dedupe_key,
    build_watch_notify_message,
)


class Command(BaseCommand):
    help = "tradeai のウォッチ監視結果をLINEで通知します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            default="",
            help="対象ユーザー名。省略時は settings.STOCKS_OWNER_USERNAME を使います。",
        )
        parser.add_argument(
            "--max-items",
            type=int,
            default=3,
            help="1回の通知に含める最大件数です。",
        )
        parser.add_argument(
            "--dedupe-minutes",
            type=int,
            default=180,
            help="同じ通知を再送しない時間（分）です。",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="送信せず、通知文のプレビューだけ表示します。",
        )

    def handle(self, *args, **options):
        username = (options.get("username") or "").strip()
        max_items = max(1, int(options.get("max_items") or 3))
        dedupe_minutes = max(1, int(options.get("dedupe_minutes") or 180))
        dry_run = bool(options.get("dry_run"))

        if not username:
            username = getattr(settings, "STOCKS_OWNER_USERNAME", "").strip()

        if not username:
            raise CommandError("対象ユーザー名がありません。--username を付けるか STOCKS_OWNER_USERNAME を設定してください。")

        User = get_user_model()

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(f"ユーザーが見つかりません: {username}") from exc

        cleanup_old_entries(days=7)

        rows = build_watch_signal_rows(user)
        candidates = [row for row in rows if row.get("emit_event")]

        sendable_rows: list[dict] = []
        sendable_keys: list[str] = []

        for row in candidates:
            dedupe_key = build_watch_notify_dedupe_key(user.id, row)
            if was_sent_recently(dedupe_key, dedupe_minutes=dedupe_minutes):
                continue
            sendable_rows.append(row)
            sendable_keys.append(dedupe_key)

        sendable_rows = sendable_rows[:max_items]
        sendable_keys = sendable_keys[:max_items]

        self.stdout.write(f"user              : {username}")
        self.stdout.write(f"all_rows           : {len(rows)}")
        self.stdout.write(f"event_candidates   : {len(candidates)}")
        self.stdout.write(f"sendable           : {len(sendable_rows)}")

        if not sendable_rows:
            self.stdout.write(self.style.WARNING("送信対象はありませんでした。"))
            return

        message = build_watch_notify_message(sendable_rows, max_items=max_items)

        if dry_run:
            self.stdout.write(self.style.SUCCESS("dry-run なので送信はしていません。"))
            self.stdout.write("----- message preview -----")
            self.stdout.write(message)
            self.stdout.write("----- end -----")
            return

        ok, detail = send_line_text(message)
        if not ok:
            raise CommandError(f"LINE送信に失敗しました: {detail}")

        mark_sent(sendable_keys)

        self.stdout.write(self.style.SUCCESS("LINE通知を送信しました。"))
        self.stdout.write(f"sent_items         : {len(sendable_rows)}")
        self.stdout.write(f"detail             : {detail}")