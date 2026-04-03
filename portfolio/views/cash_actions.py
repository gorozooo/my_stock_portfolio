# [FILE] cash_actions.py
# [PATH] portfolio/views/cash_actions.py
#
# このファイルは何？
# - 現金台帳の削除アクション
# - 現金台帳からの「手仕舞い取消」アクション
#
# 今回の方針
# - 手入力の台帳だけ削除可能
# - 現物売 / 信用返済 の台帳行だけ手仕舞い取消可能
# - 元データへリンクは使わず、ここで直接処理する

# -*- coding: utf-8 -*-
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from ..models import RealizedTrade, TradeEvent
from ..models_cash import CashLedger
from ..services.trades.close_cancel_service import cancel_close_trade_event


def _is_manual_ledger(row: CashLedger) -> bool:
    source_type = (getattr(row, "source_type", None) or "").strip()
    source_id = getattr(row, "source_id", None)
    return (source_type == "") and (not source_id)


def _is_trade_source(source_type: str) -> bool:
    return source_type.upper() in ["TRD", "TRADE", "TRADE_EVENT"]


def _is_realized_source(source_type: str) -> bool:
    return source_type.upper() in ["REAL", "REALIZED", "RT"]


@require_POST
def cash_entry_delete(request, pk: int):
    row = get_object_or_404(CashLedger, pk=pk)
    next_url = request.META.get("HTTP_REFERER") or "/cash/history/"

    if not _is_manual_ledger(row):
        messages.error(request, "この行は手入力ではないため、ここから削除できません。")
        return redirect(next_url)

    memo = row.memo or "現金台帳"
    row.delete()
    messages.success(request, f"削除しました：{memo}")
    return redirect(next_url)


@require_POST
def cash_trade_cancel(request, pk: int):
    row = get_object_or_404(CashLedger, pk=pk)
    next_url = request.META.get("HTTP_REFERER") or "/cash/history/"

    source_type = (getattr(row, "source_type", None) or "").strip()
    source_id = getattr(row, "source_id", None)

    if not source_id:
        messages.error(request, "この行は手仕舞い取消に対応していません。")
        return redirect(next_url)

    event = None

    if _is_trade_source(source_type):
        event = TradeEvent.objects.filter(pk=source_id).first()

    elif _is_realized_source(source_type):
        realized = RealizedTrade.objects.filter(pk=source_id).first()
        if realized:
            event = (
                TradeEvent.objects.filter(
                    realized_trade=realized,
                    event_type__in=[
                        TradeEvent.EventType.SPOT_SELL,
                        TradeEvent.EventType.MARGIN_CLOSE,
                    ],
                )
                .order_by("-id")
                .first()
            )

    if event is None:
        messages.error(request, "対応する手仕舞いイベントが見つかりませんでした。")
        return redirect(next_url)

    try:
        cancel_close_trade_event(event)
        messages.success(request, f"手仕舞いを取り消しました：{event.ticker}")
    except Exception as e:
        messages.error(request, f"手仕舞い取消に失敗しました：{e}")

    return redirect(next_url)