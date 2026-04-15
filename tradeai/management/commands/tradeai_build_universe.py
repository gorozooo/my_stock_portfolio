# =========================================================
# [FILE] tradeai_build_universe.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_build_universe.py
#
# このファイルは何？
# - tradeai の監視対象ユニバースを作るコマンドです。
# - 保有銘柄 / ウォッチリスト / 日経225 / TOPIX を統合します。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.services.universe.build_service import build_universe_for_user


class Command(BaseCommand):
    help = "tradeai の監視対象ユニバースを構築します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            default="",
            help="対象ユーザー名。省略時は settings.STOCKS_OWNER_USERNAME を使います。",
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

        result = build_universe_for_user(user)

        self.stdout.write(self.style.SUCCESS("tradeai ユニバース構築が完了しました。"))
        self.stdout.write(f"user              : {username}")
        self.stdout.write(f"holdings          : {result['holding_count']}")
        self.stdout.write(f"watchlist         : {result['watchlist_count']}")
        self.stdout.write(f"nikkei225         : {result['nikkei225_count']}")
        self.stdout.write(f"topix             : {result['topix_count']}")
        self.stdout.write(f"created           : {result['created_count']}")
        self.stdout.write(f"updated           : {result['updated_count']}")
        self.stdout.write(f"deactivated       : {result['deactivated_count']}")
        self.stdout.write(f"total_active      : {result['total_active_count']}")

        if result["topix_count"] == 0:
            self.stdout.write(
                self.style.WARNING(
                    "TOPIX リストはまだ 0 件です。"
                    " tradeai/data/universe/topix.txt を後で入れれば自動で統合されます。"
                )
            )