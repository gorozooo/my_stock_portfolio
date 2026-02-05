"""
[FILE] autotrade/jobs/intraday_guard.py
[PATH] <project_root>/autotrade/jobs/intraday_guard.py

設計D-1〜D-3：場中ガードを cron で回す入口。

やること：
- 今日のExecution（PAPER/LIVE）を集計して guard 判定
- 止めるべきなら state を STOP に倒す（emergency_stopではない）
- 理由を gate_reason に追記
- guardの判断ログを state.rules に保存（JSON）

注意：
- “翌朝自然復帰”のため emergency_stop は使わない（D-4）。
"""

from datetime import date

from django.utils import timezone
from django.db import transaction

from autotrade.models import AutoTradeDailyState
from autotrade.models_backtest import AutoTradeExecution
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.live.guards_intraday import evaluate_intraday_guards


@transaction.atomic
def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # 非常停止は“凍結”
    if is_emergency_stopped(state):
        return {"ok": True, "skipped": True, "reason": "emergency_stop"}

    # gateがSTOPなら guardだけ記録して終わり（上書きはしない）
    gate_level = str(state.gate_level or "STOP")

    equity = int(state.equity_yen or 1_000_000)

    # 今日のExecution（PAPER/LIVEのみ）
    qs = AutoTradeExecution.objects.filter(
        created_at__date=today,
    ).exclude(mode="BACKTEST").order_by("exit_at", "id")

    execs = list(qs)

    res = evaluate_intraday_guards(
        gate_level=gate_level,
        equity_yen=equity,
        execs_today=execs,
        now=timezone.localtime(timezone.now()),
    )

    # state.rules に guardログを保存（壊さない）
    rules = state.rules if isinstance(state.rules, dict) else {}
    rules["intraday_guard"] = {
        "ts": timezone.localtime(timezone.now()).isoformat(),
        "gate_level": gate_level,
        "result": {
            "stop_now": bool(res.stop_now),
            "forbid_new_entries": bool(res.forbid_new_entries),
            "force_close_now": bool(res.force_close_now),
            "reason": str(res.stop_reason or ""),
            "meta": res.meta,
        },
        "counts": {
            "execs_today": len(execs),
        },
    }
    state.rules = rules

    # STOPに倒す条件（当日STOP / 翌朝復帰）
    if res.stop_now and gate_level != "STOP":
        state.gate_level = "STOP"

        add = str(res.stop_reason or "").strip()
        if add:
            # gate_reason に追記（重複は避ける）
            base = (state.gate_reason or "").rstrip()
            if add not in base:
                state.gate_reason = (base + "\n" + add).strip() if base else add

        # strategyも空にして「動かない」ことを明示
        state.strategy = ""

    state.updated_at = timezone.now()
    state.save()

    return {
        "ok": True,
        "skipped": False,
        "stop_now": bool(res.stop_now),
        "forbid_new_entries": bool(res.forbid_new_entries),
        "force_close_now": bool(res.force_close_now),
        "reason": str(res.stop_reason or ""),
    }