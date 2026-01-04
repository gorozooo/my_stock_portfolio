from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from advisor.services.board_source import build_board
try:
    from advisor.models_cache import BoardCache
except Exception:
    BoardCache = None  # type: ignore

User = get_user_model()

class Command(BaseCommand):
    help = "Rebuild Advisor Board cache (forces fresh build; never caches empty)."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, default=None)
        parser.add_argument("--no-cache", action="store_true", help="do not read old cache")

    def handle(self, *args, **opts):
        user_id = opts.get("user_id")
        no_cache = bool(opts.get("no_cache"))
        user = User.objects.filter(id=user_id).first() if user_id else User.objects.first()
        if not user:
            self.stdout.write(self.style.ERROR("No user found"))
            return

        data = build_board(user, use_cache=not no_cache)
        n = len(data.get("highlights") or [])
        self.stdout.write(self.style.SUCCESS(f"Board built (items={n})"))
        # 古い壊れキャッシュの掃除（空payloadを除去）
        if BoardCache is not None:
            try:
                BoardCache.objects.filter(payload__highlights=[]).delete()
            except Exception:
                pass