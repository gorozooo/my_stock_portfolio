# =========================================================
# [FILE] tradeai_build_candidate_snapshot.py
# [PATH] <project_root>/tradeai/management/commands/tradeai_build_candidate_snapshot.py
#
# このファイルは何？
# - 候補抽出を事前計算して CandidateSnapshot に保存するコマンドです。
# - これを先に実行しておくことで、候補ページは重い計算をせず
#   保存済み結果を読むだけになります。
# =========================================================

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tradeai.models.candidate_snapshot import CandidateSnapshot
from tradeai.services.candidates.candidate_service import build_candidate_rows


def _to_jsonable(value):
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _serialize_regime(snapshot) -> dict:
    if not snapshot:
        return {}

    return {
        "as_of": snapshot.as_of.isoformat() if snapshot.as_of else "",
        "market_bias": snapshot.market_bias,
        "market_bias_label": snapshot.get_market_bias_display(),
        "long_score": float(snapshot.long_score or 0),
        "short_score": float(snapshot.short_score or 0),
        "summary_text": snapshot.summary_text or "",
        "payload": _to_jsonable(snapshot.payload or {}),
    }


class Command(BaseCommand):
    help = "tradeai の候補抽出結果を CandidateSnapshot に保存します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            required=True,
            help="対象ユーザー名",
        )
        parser.add_argument(
            "--limit-per-side",
            type=int,
            default=8,
            help="画面に保存するロング/ショート件数",
        )

    def handle(self, *args, **options):
        username = options["username"]
        limit_per_side = int(options["limit_per_side"] or 8)

        User = get_user_model()

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(f"ユーザーが見つかりません: {username}") from exc

        result = build_candidate_rows(user, limit_per_side=limit_per_side)

        snapshot = CandidateSnapshot.objects.create(
            user=user,
            score_max=int(result.get("score_max", 100) or 100),
            universe_count=int(result.get("universe_count", 0) or 0),
            candidate_total=int(result.get("candidate_total", 0) or 0),
            long_candidate_count=int(result.get("long_candidate_count", 0) or 0),
            short_candidate_count=int(result.get("short_candidate_count", 0) or 0),
            strong_candidate_count=int(result.get("strong_candidate_count", 0) or 0),
            attention_candidate_count=int(result.get("attention_candidate_count", 0) or 0),
            universe_source_counts=_to_jsonable(result.get("universe_source_counts", {})),
            candidate_source_counts=_to_jsonable(result.get("candidate_source_counts", {})),
            last_regime=_serialize_regime(result.get("last_regime")),
            debug_stats=_to_jsonable(result.get("debug_stats", {})),
            long_rows=_to_jsonable(result.get("long_rows", [])),
            short_rows=_to_jsonable(result.get("short_rows", [])),
        )

        self.stdout.write(f"user                  : {user.username}")
        self.stdout.write(f"snapshot_id           : {snapshot.id}")
        self.stdout.write(f"built_at              : {snapshot.built_at:%Y-%m-%d %H:%M:%S}")
        self.stdout.write(f"universe_count        : {snapshot.universe_count}")
        self.stdout.write(f"candidate_total       : {snapshot.candidate_total}")
        self.stdout.write(f"long_candidate_count  : {snapshot.long_candidate_count}")
        self.stdout.write(f"short_candidate_count : {snapshot.short_candidate_count}")
        self.stdout.write(self.style.SUCCESS("tradeai 候補スナップショット保存が完了しました。"))