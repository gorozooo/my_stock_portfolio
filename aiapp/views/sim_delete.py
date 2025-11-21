# aiapp/views/sim_delete.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from aiapp.models import VirtualTrade


@login_required
@require_POST
def simulate_delete(request, pk: int, *args: Any, **kwargs: Any):
    """
    AIシミュレ1件を削除するビュー。

    - POST専用
    - 削除後は一覧画面(simulate_list)に戻る
    - 「next」パラメータがあればそこを優先してリダイレクト
    """
    vt = get_object_or_404(VirtualTrade, pk=pk)
    vt.delete()

    next_url = request.POST.get("next")
    if not next_url:
        return redirect("aiapp:simulate_list")
    return redirect(next_url)