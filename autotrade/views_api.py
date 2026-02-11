"""
[FILE] autotrade/views_api.py
[PATH] <project_root>/autotrade/views_api.py

このファイルは何？
- AutoTrade のワンタップ操作系（API）をまとめた View です。
- 今回は非常停止だけを views.py から分離します。
"""

from __future__ import annotations

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import AutoTradeDailyState


@login_required
@require_POST
def api_emergency_stop(request: HttpRequest):
    """
    今日の AutoTradeDailyState を非常停止にする。
    - emergency_stop=True
    - 理由・時刻を保存
    - gate_level も STOP に倒して UI でも即わかるようにする
    """
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    if state.emergency_stop:
        return JsonResponse({
            "ok": True,
            "already": True,
            "message": "already_stopped",
        })

    state.emergency_stop = True
    state.emergency_stopped_at = timezone.now()
    state.emergency_stop_reason = "manual"

    state.gate_level = "STOP"
    add_reason = "非常停止（手動）"
    if state.gate_reason:
        if add_reason not in state.gate_reason:
            state.gate_reason = (state.gate_reason.rstrip() + "\n" + add_reason)
    else:
        state.gate_reason = add_reason

    state.updated_at = timezone.now()
    state.save()

    return JsonResponse({
        "ok": True,
        "already": False,
        "message": "stopped",
        "stopped_at": timezone.localtime(state.emergency_stopped_at).isoformat() if state.emergency_stopped_at else None,
    })