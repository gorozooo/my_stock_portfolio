# aiapp/views/sim_delete.py
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from aiapp.models.vtrade import VirtualTrade


@login_required
@require_POST
def simulate_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """
    シミュレ記録（DB: VirtualTrade）を 1 件だけ削除する。

    重要:
    - simulate_list は entries の e.id に VirtualTrade.pk を入れている
    - delete も pk を VirtualTrade.pk として扱う
    - 必ず user で絞って削除し、他ユーザー/他データ誤削除を防ぐ
    """
    v = get_object_or_404(VirtualTrade, pk=pk, user=request.user)
    v.delete()

    # 可能なら元ページへ戻す（フィルタ維持）
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if next_url:
        return redirect(next_url)

    return redirect(reverse("aiapp:simulate_list"))