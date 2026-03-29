# [FILE] holding_actions.py
# [PATH] portfolio/views/holding_actions.py
#
# このファイルは何？
# - Holding の作成/編集/削除のアクション系ビュー
# - 一覧表示ロジックは旧 holding.py に残し、更新処理だけ薄く分離する

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..forms import HoldingForm
from ..models import Holding, TradeEvent
from ..models_cash import CashLedger
from ..services.holdings.entry_service import register_holding_entry, update_holding_metadata
from ..services.cash.ledger_service import delete_trade_event_ledgers


@login_required
def holding_create(request):
    if request.method == "POST":
        form = HoldingForm(request.POST)
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.user = request.user
                res = register_holding_entry(obj)
                if res.merged:
                    messages.success(request, "同じ銘柄の既存保有に買い増しとして反映しました。")
                else:
                    messages.success(request, "保有を登録しました。")
                return redirect("holding_list")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = HoldingForm()
    return render(request, "holdings/form.html", {"form": form, "mode": "create"})


@login_required
def holding_edit(request, pk):
    obj = get_object_or_404(Holding, pk=pk, user=request.user)
    if request.method == "POST":
        form = HoldingForm(request.POST, instance=obj)
        if form.is_valid():
            try:
                cleaned_obj = form.save(commit=False)
                cleaned_obj.user = request.user
                update_holding_metadata(obj, cleaned_obj)
                messages.success(request, "保有の表示情報を更新しました。")
                return redirect("holding_list")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = HoldingForm(instance=obj)
    return render(request, "holdings/form.html", {"form": form, "mode": "edit", "obj": obj})


@login_required
@require_POST
def holding_delete(request, pk: int):
    h = get_object_or_404(Holding, pk=pk, user=request.user)

    linked_realized = h.trade_events.filter(realized_trade__isnull=False).exists()
    if linked_realized:
        messages.error(request, "この保有はクローズ履歴と連動しているため、直接削除できません。")
        return redirect("holding_list")

    event_ids = list(h.trade_events.values_list("id", flat=True))
    for ev_id in event_ids:
        delete_trade_event_ledgers(ev_id)

    h.trade_events.all().delete()
    h.delete()

    if request.headers.get("HX-Request") == "true":
        return HttpResponse("")

    messages.success(request, "保有と関連する買付イベントを削除しました。")
    return redirect("holding_list")