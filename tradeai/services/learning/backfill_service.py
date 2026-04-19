# =========================================================
# [FILE] backfill_service.py
# [PATH] <project_root>/tradeai/services/learning/backfill_service.py
#
# このファイルは何？
# - 既存のデモ建玉に対して、LearningSnapshot を後付けで補完するサービスです。
# - まずは「まだ LearningSnapshot が無い OPEN 建玉」を対象にします。
# - 既に snapshot がある建玉は重複作成しません。
# - 今回は、単一の DemoTrade から snapshot を確実に作る共通関数も追加しています。
# =========================================================

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db import transaction

from tradeai.models.demo_trade import DemoTrade
from tradeai.models.learning_snapshot import LearningSnapshot


def _to_decimal_score(value: Any) -> Decimal:
    try:
        return Decimal(str(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _pick_signal_score(entry_payload: dict[str, Any]) -> Decimal:
    """
    LearningSnapshot.signal_score に入れる値を決める。
    優先順位:
    1. score_100
    2. score_total
    3. 0
    """
    if not isinstance(entry_payload, dict):
        return Decimal("0.00")

    if entry_payload.get("score_100") is not None:
        return _to_decimal_score(entry_payload.get("score_100"))

    if entry_payload.get("score_total") is not None:
        return _to_decimal_score(entry_payload.get("score_total"))

    return Decimal("0.00")


def _pick_regime_label(entry_payload: dict[str, Any]) -> str:
    if not isinstance(entry_payload, dict):
        return ""

    value = str(entry_payload.get("regime_relation_label") or "").strip()
    return value[:32]


def _build_features_json(trade: DemoTrade) -> dict[str, Any]:
    payload = trade.entry_payload if isinstance(trade.entry_payload, dict) else {}

    return {
        "entry_price": float(trade.entry_price) if trade.entry_price is not None else None,
        "take_profit_price": float(trade.take_profit_price) if trade.take_profit_price is not None else None,
        "stop_price": float(trade.stop_price) if trade.stop_price is not None else None,
        "qty": int(trade.qty or 0),
        "last_close": payload.get("last_close"),
        "score_total": payload.get("score_total"),
        "score_100": payload.get("score_100"),
        "sector_name": payload.get("sector_name") or "",
        "flow_label": payload.get("flow_label") or "",
        "momentum_label": payload.get("momentum_label") or "",
        "big_flow_label": payload.get("big_flow_label") or "",
        "breakout_human_label": payload.get("breakout_human_label") or "",
        "volatility_label": payload.get("volatility_label") or "",
    }


def _build_signals_json(trade: DemoTrade) -> dict[str, Any]:
    payload = trade.entry_payload if isinstance(trade.entry_payload, dict) else {}

    return {
        "source_labels": list(payload.get("source_labels") or []),
        "indicator_pills": list(payload.get("indicator_pills") or []),
        "fact_reasons": list(payload.get("fact_reasons") or []),
        "decision_text": payload.get("decision_text") or "",
        "regime_relation_label": payload.get("regime_relation_label") or "",
        "entry_reason_text": trade.entry_reason_text or "",
        "demo_status": trade.status,
        "demo_result_label": trade.result_label,
    }


@transaction.atomic
def ensure_learning_snapshot_for_trade(trade: DemoTrade) -> tuple[LearningSnapshot, bool]:
    """
    1つの DemoTrade に対して LearningSnapshot を必ず1つ持たせる。
    戻り値:
    - snapshot
    - created_now: 今回新規作成したなら True
    """
    existing = (
        LearningSnapshot.objects.filter(demo_trade=trade)
        .order_by("-id")
        .first()
    )
    if existing:
        return existing, False

    payload = trade.entry_payload if isinstance(trade.entry_payload, dict) else {}

    snapshot = LearningSnapshot.objects.create(
        user=trade.user,
        signal_event=trade.signal_event,
        demo_trade=trade,
        ticker=trade.ticker,
        name=(trade.name or payload.get("display_name") or "").strip(),
        direction=trade.direction,
        source_scope=trade.source_scope,
        snapshot_at=trade.entry_at,
        signal_score=_pick_signal_score(payload),
        regime_label=_pick_regime_label(payload),
        features_json=_build_features_json(trade),
        signals_json=_build_signals_json(trade),
        was_notified=bool(trade.signal_event_id),
        was_entered=True,
    )
    return snapshot, True


@transaction.atomic
def backfill_learning_snapshots_for_user(user, open_only: bool = True) -> dict[str, Any]:
    qs = DemoTrade.objects.filter(user=user)

    if open_only:
        qs = qs.filter(status=DemoTrade.StatusChoices.OPEN)

    trades = list(qs.order_by("entry_at", "id"))

    created_snapshots: list[LearningSnapshot] = []
    skipped_existing_count = 0

    for trade in trades:
        snapshot, created_now = ensure_learning_snapshot_for_trade(trade)
        if created_now:
            created_snapshots.append(snapshot)
        else:
            skipped_existing_count += 1

    return {
        "target_trade_count": len(trades),
        "created_count": len(created_snapshots),
        "skipped_existing_count": skipped_existing_count,
        "created_snapshots": created_snapshots,
    }