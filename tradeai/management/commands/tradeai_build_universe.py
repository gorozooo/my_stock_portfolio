# =========================================================
# [FILE] tradeai_build_universe.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_build_universe.py
#
# このファイルは何？
# - tradeai の監視ユニバースを再構築する管理コマンドです。
# - 追加した growth.txt をDBへ反映するために使います。
#
# 使い方：
# - 1ユーザーだけ更新:
#   python manage.py tradeai_build_universe --username gorozooo
# - 全ユーザー更新:
#   python manage.py tradeai_build_universe
# =========================================================

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.services.universe.build_service import build_universe_for_user


class Command(BaseCommand):
    help = "tradeai の監視ユニバースを再構築します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            help="特定ユーザーだけ更新したいときに指定します。",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = (options.get("username") or "").strip()

        if username:
            try:
                users = [User.objects.get(username=username)]
            except User.DoesNotExist:
                raise CommandError(f"ユーザーが見つかりません: {username}")
        else:
            users = list(User.objects.all().order_by("id"))

        if not users:
            self.stdout.write(self.style.WARNING("対象ユーザーがいません。"))
            return

        for user in users:
            result = build_universe_for_user(user)

            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"user: {user.username}"))
            self.stdout.write(f"  created_count     : {result['created_count']}")
            self.stdout.write(f"  updated_count     : {result['updated_count']}")
            self.stdout.write(f"  deactivated_count : {result['deactivated_count']}")
            self.stdout.write(f"  holding_count     : {result['holding_count']}")
            self.stdout.write(f"  watchlist_count   : {result['watchlist_count']}")
            self.stdout.write(f"  nikkei225_count   : {result['nikkei225_count']}")
            self.stdout.write(f"  topix_count       : {result['topix_count']}")
            self.stdout.write(f"  growth_count      : {result['growth_count']}")
            self.stdout.write(f"  total_active_count: {result['total_active_count']}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("tradeai ユニバース再構築が完了しました。"))