# =========================================================
# [FILE] result_service.py
# [PATH] <project_root>/tradeai/services/learning/result_service.py
#
# このファイルは何？
# - CLOSED になった DemoTrade から LearningResult を自動作成/更新するサービスです。
# - LearningSnapshot が無ければ先に補完し、その snapshot に結果を紐づけます。
# =========================================================

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from tradeai.models.demo_trade import DemoTrade
from tradeai.models.learning_result import LearningResult
from tradeai.services.learning.backfill_service import ensure_learning_snapshot_for_trade


def _calc_hold_days(trade: DemoTrade) -> int | None:
    if not trade.entry_at or not trade.close_at:
        return None

    days = (trade.close_at.date() - trade.entry_at.date()).days
    return max(0, int(days))


def _build_result_payload(trade: DemoTrade) -> dict[str, Any]:
    entry_payload = trade.entry_payload if isinstance(trade.entry_payload, dict) else {}

    return {
        "ticker": trade.ticker,
        "name": trade.name or entry_payload.get("display_name") or "",
        "direction": trade.direction,
        "source_scope": trade.source_scope,
        "entry_at": trade.entry_at.isoformat() if trade.entry_at else None,
        "close_at": trade.close_at.isoformat() if trade.close_at else None,
        "entry_price": float(trade.entry_price) if trade.entry_price is not None else None,
        "close_price": float(trade.close_price) if trade.close_price is not None else None,
        "take_profit_price": float(trade.take_profit_price) if trade.take_profit_price is not None else None,
        "stop_price": float(trade.stop_price) if trade.stop_price is not None else None,
        "qty": int(trade.qty or 0),
        "sector_name": entry_payload.get("sector_name") or "",
        "score_total": entry_payload.get("score_total"),
        "score_100": entry_payload.get("score_100"),
        "fact_reasons": list(entry_payload.get("fact_reasons") or []),
        "indicator_pills": list(entry_payload.get("indicator_pills") or []),
        "decision_text": entry_payload.get("decision_text") or "",
        "regime_relation_label": entry_payload.get("regime_relation_label") or "",
        "entry_reason_text": trade.entry_reason_text or "",
        "exit_reason": trade.exit_reason or "",
    }


@transaction.atomic
def sync_learning_result_for_closed_trade(trade: DemoTrade) -> dict[str, Any]:
    """
    CLOSED済み DemoTrade から LearningResult を同期する。
    - snapshot が無ければ自動補完
    - result は create / update_or_create で確実に保持
    """
    if trade.status != DemoTrade.StatusChoices.CLOSED:
        return {
            "snapshot": None,
            "snapshot_created": False,
            "result": None,
            "result_created": False,
        }

    snapshot, snapshot_created = ensure_learning_snapshot_for_trade(trade)

    result, result_created = LearningResult.objects.update_or_create(
        snapshot=snapshot,
        defaults={
            "settled_at": trade.close_at or timezone.now(),
            "result_label": trade.result_label or LearningResult.ResultLabelChoices.UNKNOWN,
            "hold_days": _calc_hold_days(trade),
            "pnl_yen": trade.pnl_yen,
            "pnl_pct": trade.pnl_pct,
            "max_favorable_pct": trade.max_favorable_pct,
            "max_adverse_pct": trade.max_adverse_pct,
            "exit_reason": (trade.exit_reason or "").strip(),
            "result_payload": _build_result_payload(trade),
        },
    )

    return {
        "snapshot": snapshot,
        "snapshot_created": snapshot_created,
        "result": result,
        "result_created": result_created,
    }