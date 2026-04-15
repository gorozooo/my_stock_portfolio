# =========================================================
# [FILE] tradeai_watch_holdings.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_watch_holdings.py
#
# このファイルは何？
# - 保有銘柄監視を実行し、必要なものだけ SignalEvent に保存するコマンドです。
# - 継続保有は保存せず、警戒/候補だけ履歴化します。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tradeai.models.signal_event import SignalEvent
from tradeai.services.holdings.monitor_service import build_holding_monitor_rows


class Command(BaseCommand):
    help = "tradeai の保有銘柄監視を実行し、警戒/候補シグナルを保存します。"

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

        rows = build_holding_monitor_rows(user)
        now = timezone.now()

        resolved_count = SignalEvent.objects.filter(
            user=user,
            scope=SignalEvent.ScopeChoices.HOLDING,
            status=SignalEvent.StatusChoices.OPEN,
        ).update(
            status=SignalEvent.StatusChoices.RESOLVED,
            resolved_at=now,
        )

        created_count = 0
        attention_count = 0
        strong_count = 0

        for row in rows:
            if not row["emit_event"]:
                continue

            SignalEvent.objects.create(
                user=user,
                holding=row["holding"],
                ticker=row["ticker"],
                name=row["name"],
                scope=SignalEvent.ScopeChoices.HOLDING,
                direction=row["direction"],
                level=row["level"],
                status=SignalEvent.StatusChoices.OPEN,
                score_total=row["score_total"],
                regime_label="HOLDING_MONITOR",
                reason_text=row["reason_text"],
                signals_payload={
                    "monitor_kind": "holding",
                    "action_key": row["action_key"],
                    "action_label": row["action_label"],
                    "direction": row["direction"],
                    "broker": row["broker"],
                    "account": row["account"],
                    "quantity": row["quantity"],
                    "avg_cost": row["avg_cost"],
                    "last_price": row["last_price"],
                    "hold_days": row["hold_days"],
                    "pnl_pct": row["pnl_pct"],
                    "pnl_yen": row["pnl_yen"],
                },
                event_at=now,
            )
            created_count += 1

            if row["level"] == SignalEvent.LevelChoices.STRONG:
                strong_count += 1
            elif row["level"] == SignalEvent.LevelChoices.ATTENTION:
                attention_count += 1

        self.stdout.write(self.style.SUCCESS("tradeai 保有銘柄監視が完了しました。"))
        self.stdout.write(f"user              : {username}")
        self.stdout.write(f"rows              : {len(rows)}")
        self.stdout.write(f"resolved_old      : {resolved_count}")
        self.stdout.write(f"created_events    : {created_count}")
        self.stdout.write(f"attention_events  : {attention_count}")
        self.stdout.write(f"strong_events     : {strong_count}")