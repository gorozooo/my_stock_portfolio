# =========================================================
# [FILE] tradeai_notify_holdings.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_notify_holdings.py
#
# このファイルは何？
# - 保有監視の結果から、LINE通知を送るコマンドです。
# - 同じ内容が短時間に何度も送られないよう、重複抑制も行います。
# - 送信対象は「emit_event=True」のものだけです。
# - Flex Message を優先して送信し、失敗時はテキストへフォールバックします。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.services.holdings.monitor_service import build_holding_monitor_rows
from tradeai.services.holdings.notify_builder import (
    build_holding_notify_dedupe_key,
    build_holding_notify_flex,
    build_holding_notify_message,
)
from tradeai.services.notify.dedupe_store import (
    cleanup_old_entries,
    mark_sent,
    was_sent_recently,
)
from tradeai.services.notify.line_push_service import send_line_flex, send_line_text


class Command(BaseCommand):
    help = "tradeai の保有監視結果をLINEで通知します。"

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

        rows = build_holding_monitor_rows(user)
        candidates = [row for row in rows if row.get("emit_event")]

        sendable_rows: list[dict] = []
        sendable_keys: list[str] = []

        for row in candidates:
            dedupe_key = build_holding_notify_dedupe_key(user.id, row)
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

        text_message = build_holding_notify_message(sendable_rows, max_items=max_items)
        flex_payload = build_holding_notify_flex(sendable_rows, max_items=max_items)

        if dry_run:
            self.stdout.write(self.style.SUCCESS("dry-run なので送信はしていません。"))
            self.stdout.write("----- text preview -----")
            self.stdout.write(text_message)
            self.stdout.write("----- end -----")
            self.stdout.write(f"flex_bubbles       : {len(flex_payload.get('contents', {}).get('contents', []))}")
            return

        ok, detail = send_line_flex(
            alt_text=flex_payload["alt_text"],
            contents=flex_payload["contents"],
        )

        if ok:
            mark_sent(sendable_keys)
            self.stdout.write(self.style.SUCCESS("Flex Message を送信しました。"))
            self.stdout.write(f"sent_items         : {len(sendable_rows)}")
            self.stdout.write(f"detail             : {detail}")
            return

        self.stdout.write(self.style.WARNING(f"Flex送信に失敗したのでテキストへ切り替えます: {detail}"))

        ok_text, detail_text = send_line_text(text_message)
        if not ok_text:
            raise CommandError(f"LINE送信に失敗しました。Flex: {detail} / Text: {detail_text}")

        mark_sent(sendable_keys)

        self.stdout.write(self.style.SUCCESS("テキスト通知で送信しました。"))
        self.stdout.write(f"sent_items         : {len(sendable_rows)}")
        self.stdout.write(f"detail             : {detail_text}")