# =========================================================
# [FILE] auto_entry_service.py
# [PATH] <project_root>/tradeai/services/demo/auto_entry_service.py
#
# このファイルは何？
# - 候補抽出結果から、デモ建玉を自動作成するサービスです。
# - ロング上位 / ショート上位を自動でOPENします。
#
# 今回の修正：
# - LearningResult を見て、
#   成績が良かった条件を少し優先する「学習バイアス」を追加しています。
# - ただし件数が少ないうちは効かない安全設計です。
# =========================================================

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db import transaction

from tradeai.models.demo_trade import DemoTrade
from tradeai.services.candidates.candidate_service import build_candidate_rows
from tradeai.services.learning.bias_service import (
    build_learning_bias_context,
    evaluate_candidate_learning_bias,
)


DEFAULT_OPEN_PER_SIDE = 3
DEFAULT_QTY = 100


def _to_decimal_price(value: Any) -> Decimal:
    return Decimal(str(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _resolve_source_scope(row: dict[str, Any]) -> str:
    labels = list(row.get("source_labels") or [])

    if "保有" in labels:
        return DemoTrade.SourceScopeChoices.HOLDING
    if "ウォッチ" in labels:
        return DemoTrade.SourceScopeChoices.WATCHLIST
    return DemoTrade.SourceScopeChoices.UNIVERSE


def _build_entry_reason_text(row: dict[str, Any]) -> str:
    fact_reasons = list(row.get("fact_reasons") or [])
    if fact_reasons:
        return " / ".join(fact_reasons[:4])

    decision_text = str(row.get("decision_text") or "").strip()
    if decision_text:
        return decision_text

    action_label = str(row.get("action_label") or "").strip()
    return action_label


def _build_entry_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "score_total": row.get("score_total"),
        "score_100": row.get("score_100"),
        "adjusted_score_100": row.get("adjusted_score_100"),
        "learning_bias_total": row.get("learning_bias_total"),
        "learning_bias_notes": list(row.get("learning_bias_notes") or []),
        "learning_score_band": row.get("learning_score_band") or "",
        "display_name": row.get("display_name") or row.get("name") or "",
        "sector_name": row.get("sector_name") or "",
        "source_labels": list(row.get("source_labels") or []),
        "indicator_pills": list(row.get("indicator_pills") or []),
        "fact_reasons": list(row.get("fact_reasons") or []),
        "decision_text": row.get("decision_text") or "",
        "entry_price": row.get("entry_price"),
        "tp_price": row.get("tp_price"),
        "sl_price": row.get("sl_price"),
        "last_close": row.get("last_close"),
        "flow_label": row.get("flow_label"),
        "momentum_label": row.get("momentum_label"),
        "big_flow_label": row.get("big_flow_label"),
        "breakout_human_label": row.get("breakout_human_label"),
        "volatility_label": row.get("volatility_label"),
        "regime_relation_label": row.get("regime_relation_label"),
    }


def _apply_learning_bias_to_rows(rows: list[dict[str, Any]], learning_context: dict[str, Any]) -> list[dict[str, Any]]:
    adjusted_rows: list[dict[str, Any]] = []

    for row in rows:
        source_scope = _resolve_source_scope(row)
        score_100 = int(row.get("score_100") or 0)

        bias_info = evaluate_candidate_learning_bias(
            learning_context,
            direction=str(row.get("chosen_direction") or "").upper().strip(),
            source_scope=source_scope,
            score_100=score_100,
        )

        new_row = dict(row)
        new_row["learning_bias_total"] = int(bias_info.get("total_bias") or 0)
        new_row["learning_bias_notes"] = list(bias_info.get("notes") or [])
        new_row["learning_score_band"] = str(bias_info.get("score_band_label") or "")
        new_row["adjusted_score_100"] = score_100 + int(bias_info.get("total_bias") or 0)
        adjusted_rows.append(new_row)

    return adjusted_rows


def _sort_rows_for_auto_entry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -(int(row.get("adjusted_score_100") or 0)),
            -(int(row.get("score_100") or 0)),
            -(int(row.get("score_total") or 0)),
            int(row.get("source_rank") or 9),
            int(row.get("priority") or 999),
            str(row.get("ticker") or ""),
        ),
    )


def _iter_selected_rows(candidate_context: dict[str, Any], per_side: int, user) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    learning_context = build_learning_bias_context(user=user)

    long_rows = list(candidate_context.get("long_rows") or [])
    short_rows = list(candidate_context.get("short_rows") or [])

    adjusted_long_rows = _apply_learning_bias_to_rows(long_rows, learning_context)
    adjusted_short_rows = _apply_learning_bias_to_rows(short_rows, learning_context)

    selected: list[dict[str, Any]] = []
    selected.extend(_sort_rows_for_auto_entry(adjusted_long_rows)[:per_side])
    selected.extend(_sort_rows_for_auto_entry(adjusted_short_rows)[:per_side])

    return selected, learning_context


@transaction.atomic
def auto_open_demo_trades_for_user(user, per_side: int = DEFAULT_OPEN_PER_SIDE, qty: int = DEFAULT_QTY) -> dict[str, Any]:
    context = build_candidate_rows(user, limit_per_side=max(per_side, 8))

    selected_rows, learning_context = _iter_selected_rows(
        candidate_context=context,
        per_side=per_side,
        user=user,
    )

    open_tickers = set(
        DemoTrade.objects.filter(
            user=user,
            status=DemoTrade.StatusChoices.OPEN,
        ).values_list("ticker", flat=True)
    )

    created_trades: list[DemoTrade] = []
    skipped_existing_count = 0
    skipped_missing_plan_count = 0

    for row in selected_rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        direction = str(row.get("chosen_direction") or "").upper().strip()

        entry_price = row.get("entry_price")
        tp_price = row.get("tp_price")
        sl_price = row.get("sl_price")

        if not ticker or direction not in {
            DemoTrade.DirectionChoices.LONG,
            DemoTrade.DirectionChoices.SHORT,
        }:
            skipped_missing_plan_count += 1
            continue

        if ticker in open_tickers:
            skipped_existing_count += 1
            continue

        if entry_price is None or tp_price is None or sl_price is None:
            skipped_missing_plan_count += 1
            continue

        trade = DemoTrade.objects.create(
            user=user,
            ticker=ticker,
            name=str(row.get("display_name") or row.get("name") or "").strip(),
            direction=direction,
            source_scope=_resolve_source_scope(row),
            status=DemoTrade.StatusChoices.OPEN,
            result_label=DemoTrade.ResultLabelChoices.UNKNOWN,
            entry_price=_to_decimal_price(entry_price),
            stop_price=_to_decimal_price(sl_price),
            take_profit_price=_to_decimal_price(tp_price),
            qty=max(1, int(qty)),
            entry_reason_text=_build_entry_reason_text(row),
            entry_payload=_build_entry_payload(row),
        )
        created_trades.append(trade)
        open_tickers.add(ticker)

    return {
        "candidate_total": int(context.get("candidate_total") or 0),
        "learning_result_count": int(learning_context.get("total_result_count") or 0),
        "learning_min_count": int(learning_context.get("min_count") or 0),
        "created_count": len(created_trades),
        "skipped_existing_count": skipped_existing_count,
        "skipped_missing_plan_count": skipped_missing_plan_count,
        "created_trades": created_trades,
    }