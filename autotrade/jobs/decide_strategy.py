"""
[FILE] autotrade/jobs/decide_strategy.py
[PATH] <project_root>/autotrade/jobs/decide_strategy.py

このファイルは何？
- 9:30 に動く「戦略決定＆ルール確定ジョブ」です。

重要（ここが今回の修正点）：
- gate判定は朝（morning_prepare/runner）でExecution基準で確定済み。
- 9:30 job は gate を再計算しない（上書き事故を防ぐ）。
- 9:30 は「朝統計で戦略を選ぶ」＋「gateのactiveに無い戦略は起動しない」＋「rules確定」だけ。

今回の変更：
- state.backtest の新フォーマット（meta/by_window/gate）に合わせる
- gate_reason を 9:30 で壊さない（朝の判定理由をそのまま使う）
"""

from datetime import date
from django.conf import settings
from django.utils import timezone

from autotrade.models import AutoTradeDailyState
from autotrade.services.decision.strategy import decide_strategy_for_state
from autotrade.services.decision.rules import build_rules_for_today
from autotrade.services.common.guards import is_emergency_stopped


def _get_gate_final_from_state(state: AutoTradeDailyState):
    """
    morning_prepare が作った backtest['gate']['final'] を読む。
    無い場合は安全にSTOP扱い。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}
    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}
    gate_level = str(final.get("gate_level") or state.gate_level or "STOP")
    active = list(final.get("active") or [])
    disabled = list(final.get("disabled") or [])
    return gate_level, active, disabled


def run():
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # =========================================================
    # 0) 非常停止ガード（最優先）
    # =========================================================
    if is_emergency_stopped(state):
        return

    # =========================================================
    # 1) 朝統計を 9:30 時点で固定保存（再現性の要）
    # =========================================================
    try:
        from autotrade.services.universe.morning_data_service import get_morning_stats
        state.morning_stats = get_morning_stats(state=state) or {}
    except Exception:
        state.morning_stats = {
            "range_pct": 0.0,
            "trend_pct": 0.0,
            "chop_ratio": 0.0,
            "tickers_used": [],
            "n_used": 0,
            "note": "error",
        }

    # =========================================================
    # 2) 戦略決定（朝統計ベース）
    # =========================================================
    decision = decide_strategy_for_state(state)
    wanted = str(decision.strategy or "")

    # =========================================================
    # 3) gate は「朝のExecution基準」を正として読む（再計算しない）
    # =========================================================
    gate_level, active, disabled = _get_gate_final_from_state(state)

    # gateがSTOPなら、戦略は空（場中は動かさない）
    if str(gate_level) == "STOP":
        chosen = ""
    else:
        # gateのactiveに入ってる戦略だけ起動可
        if wanted in active:
            chosen = wanted
        else:
            # 9:30の判定が無効なら、activeの先頭（通常VWAP）へフォールバック
            chosen = active[0] if active else ""

    # =========================================================
    # 4) state を更新（gate_reasonは壊さない）
    # =========================================================
    state.strategy = chosen
    state.strategy_decided_at = timezone.now()

    state.strategy_decision = {
        "strategy_wanted": wanted,
        "strategy": chosen,
        "confidence": float(decision.confidence),
        "reason": decision.reason,
        "debug": decision.debug,
        "gate_level": gate_level,
        "gate_active": active,
        "gate_disabled": disabled,
        "created_at": timezone.localtime(timezone.now()).isoformat(),
    }

    # ルール確定（gate_level と chosen を使う）
    equity_yen = state.equity_yen or getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)
    state.rules = build_rules_for_today(
        gate_level=str(gate_level),
        strategy=str(chosen) if chosen else None,
        equity_yen=int(equity_yen),
    )

    # gate_levelは朝の結果を採用（stateに入ってなければ補完）
    state.gate_level = str(gate_level)

    # gate_reasonは朝のものを尊重（ここで上書きしない）
    # ただし gate_reason が空なら、最低限の説明だけ入れる
    if not (state.gate_reason or "").strip():
        if gate_level == "STOP":
            state.gate_reason = "【最終判定】STOP（安全のため稼働しない）"
        elif gate_level == "LIGHT":
            state.gate_reason = "【最終判定】LIGHT（慎重に稼働）"
        else:
            state.gate_reason = "【最終判定】FULL（通常稼働）"

    state.updated_at = timezone.now()
    state.save()