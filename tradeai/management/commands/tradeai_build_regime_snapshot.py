# =========================================================
# [FILE] tradeai_build_regime_snapshot.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_build_regime_snapshot.py
#
# このファイルは何？
# - 最新の地合いスナップショットを手動で作る管理コマンドです。
# - ダッシュボードでも自動作成しますが、手動確認や cron 用にも使えます。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.services.regime.regime_service import build_regime_snapshot


class Command(BaseCommand):
    help = "tradeai の地合いスナップショットを作成します。"

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

        snapshot = build_regime_snapshot(user)
        if not snapshot:
            raise CommandError("地合いスナップショットの作成に失敗しました。データ取得を確認してください。")

        self.stdout.write(self.style.SUCCESS("地合いスナップショットを作成しました。"))
        self.stdout.write(f"market_bias       : {snapshot.get_market_bias_display()}")
        self.stdout.write(f"long_score        : {snapshot.long_score}")
        self.stdout.write(f"short_score       : {snapshot.short_score}")
        self.stdout.write(f"summary           : {snapshot.summary_text}")