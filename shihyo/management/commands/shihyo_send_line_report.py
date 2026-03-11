"""
[FILE] shihyo_send_line_report.py
[PATH] <project_root>/shihyo/management/commands/shihyo_send_line_report.py

このファイルは何？
- 指標LINEレポート送信コマンドです。
- コマンド自身は薄く保ち、
  1) データ収集
  2) Flex組み立て
  3) LINE送信
  をサービスへ委譲します。

使い方:
- 送信せず確認
  python manage.py shihyo_send_line_report --dry-run
- 実送信
  python manage.py shihyo_send_line_report
"""

from __future__ import annotations

import json
import os

from django.core.management.base import BaseCommand, CommandParser

from shihyo.services.line_messaging_service import push_flex_message
from shihyo.services.line_report_data_service import build_line_report_context
from shihyo.services.line_report_flex_service import build_alt_text, build_flex_contents


class Command(BaseCommand):
    help = "指標ダッシュボードと同じ基準で LINE へ shihyo レポートを Flex Message で送信します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="LINE送信せず altText と Flex JSON を表示する。",
        )
        parser.add_argument(
            "--to-user-id",
            type=str,
            default="",
            help="送信先LINE userId。未指定なら環境変数 LINE_USER_ID を使う。",
        )

    def handle(self, *args, **options):
        report = build_line_report_context()
        if not report:
            self.stdout.write(self.style.ERROR("指標レポート元データがありません。先に shihyo_fetch を実行してください。"))
            return

        alt_text = build_alt_text(report)
        flex_contents = build_flex_contents(report)

        if options.get("dry_run"):
            self.stdout.write("===== altText =====")
            self.stdout.write(alt_text)
            self.stdout.write("")
            self.stdout.write("===== flex json =====")
            self.stdout.write(json.dumps(flex_contents, ensure_ascii=False, indent=2))
            return

        channel_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
        to_user_id = str(options.get("to_user_id") or os.environ.get("LINE_USER_ID") or "").strip()

        if not channel_access_token:
            self.stdout.write(self.style.ERROR("LINE_CHANNEL_ACCESS_TOKEN が設定されていません。"))
            return

        if not to_user_id:
            self.stdout.write(self.style.ERROR("送信先 userId がありません。--to-user-id または LINE_USER_ID を設定してください。"))
            return

        ok, status_code, response_text = push_flex_message(
            channel_access_token=channel_access_token,
            to_user_id=to_user_id,
            alt_text=alt_text,
            flex_contents=flex_contents,
        )

        if ok:
            self.stdout.write(self.style.SUCCESS("[shihyo_send_line_report] sent"))
            return

        self.stdout.write(
            self.style.ERROR(
                f"[shihyo_send_line_report] failed status={status_code} body={response_text}"
            )
        )