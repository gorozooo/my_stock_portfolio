"""
[FILE] autotrade/views_api.py
[PATH] <project_root>/autotrade/views_api.py

このファイルは何？
- AutoTrade のワンタップ操作系（API）をまとめた View です。
- 非常停止と、DEMO / LIVE モード切替を扱います。

今回の変更：
- api_set_execution_mode を追加
- プロ向け安全装置として、open/pending がある間は mode切替を拒否
- LIVE は今は「準備モード」扱いで、実発注層未接続の前提を崩さない
"""

from __future__ import annotations

import json
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import AutoTradeDailyState, AutoTradeSettingSnapshot


def _get_rules_dict(state: AutoTradeDailyState):
    return state.rules if isinstance(state.rules, dict) else {}


def _get_runtime_counts(rules: dict, mode: str) -> dict:
    mode_up = str(mode or "PAPER").upper().strip()
    prefix = "live" if mode_up == "LIVE" else "paper"

    open_positions = rules.get(f"{prefix}_open_positions")
    pending_orders = rules.get(f"{prefix}_pending_orders")

    open_positions = open_positions if isinstance(open_positions, dict) else {}
    pending_orders = pending_orders if isinstance(pending_orders, dict) else {}

    return {
        "open_count": len(open_positions),
        "pending_count": len(pending_orders),
    }


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


@login_required
@require_POST
def api_set_execution_mode(request: HttpRequest):
    """
    DEMO / LIVE の切替API。

    安全装置：
    - open / pending がある間は切替禁止
    - ACTIVE Snapshot が無い状態では LIVE へは切替不可
    - LIVEは今は「準備モード」。job側が安全停止ログだけ残して止まる
    """
    today = timezone.localdate()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    target_mode = str(payload.get("mode") or request.POST.get("mode") or "").upper().strip()
    if target_mode not in ["PAPER", "LIVE"]:
        return JsonResponse({
            "ok": False,
            "message": "invalid_mode",
            "detail": "mode は PAPER / LIVE のどちらかだけです。",
        }, status=400)

    rules = _get_rules_dict(state)
    current_mode = str(rules.get("execution_mode") or "PAPER").upper().strip()
    if current_mode not in ["PAPER", "LIVE"]:
        current_mode = "PAPER"

    if target_mode == current_mode:
        return JsonResponse({
            "ok": True,
            "already": True,
            "mode": current_mode,
            "detail": "すでにそのモードです。",
        })

    runtime_counts = _get_runtime_counts(rules, current_mode)
    if runtime_counts["open_count"] > 0 or runtime_counts["pending_count"] > 0:
        return JsonResponse({
            "ok": False,
            "message": "runtime_not_empty",
            "detail": "open中またはpending中の注文があるため、途中でモード切替できません。",
            "open_count": runtime_counts["open_count"],
            "pending_count": runtime_counts["pending_count"],
        }, status=400)

    if target_mode == "LIVE":
        active_snapshot = (
            AutoTradeSettingSnapshot.objects
            .filter(user=request.user, status="ACTIVE")
            .order_by("-id")
            .first()
        )
        if active_snapshot is None:
            return JsonResponse({
                "ok": False,
                "message": "no_active_snapshot",
                "detail": "ACTIVE Snapshot が無いので、LIVEへは切替できません。",
            }, status=400)

    mode_switch_logs = rules.get("mode_switch_logs")
    if not isinstance(mode_switch_logs, list):
        mode_switch_logs = []

    mode_switch_logs.append({
        "ts": timezone.localtime(timezone.now()).isoformat(),
        "from": current_mode,
        "to": target_mode,
    })

    rules["execution_mode"] = target_mode
    rules["mode_switch_logs"] = mode_switch_logs[-50:]

    state.rules = rules
    state.updated_at = timezone.now()
    state.save(update_fields=["rules", "updated_at"])

    detail = "DEMOへ切替しました。"
    if target_mode == "LIVE":
        detail = "LIVEへ切替しました。現段階では安全装置により、実発注は行わず状態確認モードになります。"

    return JsonResponse({
        "ok": True,
        "already": False,
        "mode": target_mode,
        "detail": detail,
    })