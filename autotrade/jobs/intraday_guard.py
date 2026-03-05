# =========================================================
# [FILE] intraday_guard.py
# [PATH] <project_root>/autotrade/jobs/intraday_guard.py
#
# このファイルは何？
# - 設計D-1〜D-3：場中ガードを cron で回す入口。
#
# 今回の修正：
# - trade/guard の同時実行で SQLite が lock しやすいので、
#   Python側で “排他ロック（flock -n相当）” を導入し、同時実行を防止。
# - SQLite が一瞬 lock するケースに備え、保存系だけ軽いリトライを追加。
# =========================================================

from datetime import date
import os
import time

from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.utils import OperationalError

from autotrade.models import AutoTradeDailyState
from autotrade.models_backtest import AutoTradeExecution
from autotrade.services.common.guards import is_emergency_stopped
from autotrade.services.live.guards_intraday import evaluate_intraday_guards
from autotrade.services.common.cron_lock import cron_file_lock


def _db_retry(func, *, tries: int = 6, sleep_sec: float = 0.25):
    last = None
    for _ in range(max(1, int(tries))):
        try:
            return func()
        except OperationalError as e:
            msg = str(e).lower()
            if "database is locked" not in msg:
                raise
            last = e
            time.sleep(float(sleep_sec))
    if last:
        raise last


@transaction.atomic
def run():
    # ★ 追加：trade/guardの同時実行を潰す（flock -n相当）
    lock_dir = str(getattr(settings, "AUTOTRADE_CRON_LOCK_DIR", "/tmp"))
    lock_path = os.path.join(lock_dir, "autotrade_intraday.lock")
    with cron_file_lock(lock_path) as acquired:
        if not acquired:
            return {"ok": True, "skipped": True, "reason": "locked_by_other_job"}

        today = date.today()

        def _load_state():
            return AutoTradeDailyState.objects.get_or_create(date=today)

        state, _ = _db_retry(_load_state)

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
        _db_retry(lambda: state.save())

        return {
            "ok": True,
            "skipped": False,
            "stop_now": bool(res.stop_now),
            "forbid_new_entries": bool(res.forbid_new_entries),
            "force_close_now": bool(res.force_close_now),
            "reason": str(res.stop_reason or ""),
        }