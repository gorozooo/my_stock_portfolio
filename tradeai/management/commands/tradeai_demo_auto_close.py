# =========================================================
# [FILE] tradeai_demo_auto_close.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_demo_auto_close.py
#
# このファイルは何？
# - OPEN中のデモ建玉を自動でCLOSEする管理コマンドです。
# - 引け後などに実行する想定です。
#
# 今回の修正：
# - 「まだ翌営業日足がなくて判定できない件数」と
#   「本当に価格取得に失敗した件数」を分けて表示します。
# =========================================================

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.services.demo.auto_exit_service import auto_close_demo_trades_for_user


class Command(BaseCommand):
    help = "OPEN中のデモ建玉を自動CLOSEします。"

    def add_arguments(self, parser):
        parser.add_argument("--username", type=str, default="", help="対象ユーザー名")
        parser.add_argument("--max-hold-bars", type=int, default=10, help="最大保有営業日数")

    def handle(self, *args, **options):
        username = str(options.get("username") or "").strip()
        max_hold_bars = int(options.get("max_hold_bars") or 10)

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
            result = auto_close_demo_trades_for_user(
                user=user,
                max_hold_bars=max_hold_bars,
            )

            self.stdout.write("")
            self.stdout.write(f"user              : {user.username}")
            self.stdout.write(f"open_before       : {result['open_count_before']}")
            self.stdout.write(f"closed_count      : {result['closed_count']}")
            self.stdout.write(f"kept_open_count   : {result['kept_open_count']}")
            self.stdout.write(f"not_ready_count   : {result['not_ready_count']}")
            self.stdout.write(f"price_error_count : {result['price_error_count']}")

            not_ready_trades = list(result.get("not_ready_trades") or [])
            if not_ready_trades:
                self.stdout.write("not_ready_trades:")
                for trade in not_ready_trades:
                    self.stdout.write(
                        f"  - {trade.ticker} / {trade.direction} / "
                        f"entry_at {trade.entry_at:%Y-%m-%d %H:%M}"
                    )

            price_error_trades = list(result.get("price_error_trades") or [])
            if price_error_trades:
                self.stdout.write("price_error_trades:")
                for trade in price_error_trades:
                    self.stdout.write(
                        f"  - {trade.ticker} / {trade.direction} / "
                        f"entry_at {trade.entry_at:%Y-%m-%d %H:%M}"
                    )

            closed_trades = list(result.get("closed_trades") or [])
            if closed_trades:
                self.stdout.write("closed_trades:")
                for trade in closed_trades:
                    self.stdout.write(
                        f"  - {trade.ticker} / {trade.direction} / "
                        f"{trade.result_label} / Close {trade.close_price} / "
                        f"P/L {trade.pnl_yen} / {trade.exit_reason}"
                    )