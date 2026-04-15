# =========================================================
# [FILE] tradeai_watch_signals.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_watch_signals.py
#
# このファイルは何？
# - ウォッチリスト監視を実行し、
#   注目シグナルだけ SignalEvent に保存するコマンドです。
# - 今回は VWAP・出来高急増・高値/安値ブレイク も保存します。
# =========================================================

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tradeai.models.signal_event import SignalEvent
from tradeai.services.watchlist.monitor_service import build_watch_signal_rows


class Command(BaseCommand):
    help = "tradeai のウォッチリスト監視を実行し、注目シグナルを保存します。"

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

        rows = build_watch_signal_rows(user)
        now = timezone.now()

        resolved_count = SignalEvent.objects.filter(
            user=user,
            scope=SignalEvent.ScopeChoices.WATCHLIST,
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

            level = SignalEvent.LevelChoices.STRONG if row["level"] == "STRONG" else SignalEvent.LevelChoices.ATTENTION

            SignalEvent.objects.create(
                user=user,
                watchlist_item=row["watchlist_item"],
                ticker=row["ticker"],
                name=row["name"],
                scope=SignalEvent.ScopeChoices.WATCHLIST,
                direction=row["chosen_direction"],
                level=level,
                status=SignalEvent.StatusChoices.OPEN,
                score_total=max(row["long_score"], row["short_score"]),
                regime_label="WATCHLIST_MONITOR",
                reason_text=row["summary_text"],
                signals_payload={
                    "monitor_kind": "watchlist",
                    "chosen_direction": row["chosen_direction"],
                    "long_score": row["long_score"],
                    "short_score": row["short_score"],
                    "flow_label": row["flow_label"],
                    "momentum_label": row["momentum_label"],
                    "timing_label": row["timing_label"],
                    "volatility_label": row["volatility_label"],
                    "cost_position_label": row["cost_position_label"],
                    "volume_human_label": row["volume_human_label"],
                    "breakout_human_label": row["breakout_human_label"],
                    "ma_state": row["ma_state"],
                    "ma_label": row["ma_label"],
                    "macd_state": row["macd_state"],
                    "macd_label": row["macd_label"],
                    "rsi_state": row["rsi_state"],
                    "rsi_label": row["rsi_label"],
                    "rsi": row["rsi"],
                    "atr": row["atr"],
                    "atr_pct": row["atr_pct"],
                    "vwap": row["vwap"],
                    "vwap_state": row["vwap_state"],
                    "vwap_label": row["vwap_label"],
                    "volume_ratio": row["volume_ratio"],
                    "volume_state": row["volume_state"],
                    "volume_label": row["volume_label"],
                    "breakout_state": row["breakout_state"],
                    "breakout_label": row["breakout_label"],
                    "range_high": row["range_high"],
                    "range_low": row["range_low"],
                    "selected_reasons": row["selected_reasons"],
                    "last_close": row["last_close"],
                },
                event_at=now,
            )
            created_count += 1

            if row["level"] == "STRONG":
                strong_count += 1
            else:
                attention_count += 1

        self.stdout.write(self.style.SUCCESS("tradeai ウォッチリスト監視が完了しました。"))
        self.stdout.write(f"user              : {username}")
        self.stdout.write(f"rows              : {len(rows)}")
        self.stdout.write(f"resolved_old      : {resolved_count}")
        self.stdout.write(f"created_events    : {created_count}")
        self.stdout.write(f"attention_events  : {attention_count}")
        self.stdout.write(f"strong_events     : {strong_count}")