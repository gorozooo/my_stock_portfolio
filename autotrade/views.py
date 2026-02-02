from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import AutoTradeDailyState


def _build_bt_rows_for_template(state: AutoTradeDailyState):
    """
    テンプレ側で「数字キー参照」などで詰まらないように、
    表示用に整形して返す。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    strategy = state.strategy or "BREAKOUT"
    s_bt = bt.get(strategy, {}) if isinstance(bt, dict) else {}

    rows = []
    for w in ["20", "60", "120"]:
        r = s_bt.get(w, {}) if isinstance(s_bt, dict) else {}
        rows.append({
            "window": w,
            "pf": r.get("pf"),
            "max_dd": r.get("max_dd"),
            "trades": r.get("trades"),
            "pnl": r.get("pnl"),
        })
    return rows


@login_required
def dashboard(request):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    ctx = {
        "state": state,
        "bt_rows": _build_bt_rows_for_template(state),
    }
    return render(request, "autotrade/dashboard.html", ctx)


# =========================================================
# ★ 非常停止 API（ワンタップ）
# =========================================================
@login_required
@require_POST
def api_emergency_stop(request):
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

    # UIが即「停止」になるように倒す（理由は既存があれば追記）
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