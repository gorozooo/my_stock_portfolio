"""
[FILE] autotrade/views_dashboard.py
[PATH] <project_root>/autotrade/views_dashboard.py

このファイルは何？
- AutoTrade のダッシュボード表示（iPhone 1画面）用の View と整形関数です。
- runner が state.backtest に詰めた結果（meta/by_window/gate）を “再計算せず” 表示用に整形します。
"""

from __future__ import annotations

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import render

from .models import AutoTradeDailyState
from .views_utils import get_nested_dict


def build_bt_rows_by_strategy_for_template(state: AutoTradeDailyState):
    """
    backtest の新フォーマット（meta/by_window/gate）から
    「BREAKOUT × window」の表示行を整形して返す。

    返り値:
    {
      "BREAKOUT": [ {window, trades, win_rate, pf, dd_pct, dd_yen, pnl}, ... ],
    }
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    by_window = bt.get("by_window") if isinstance(bt.get("by_window"), dict) else {}

    windows = ["20", "60", "120"]
    out = {"BREAKOUT": []}

    rows = []
    for w in windows:
        m = get_nested_dict(by_window, int(w), "BREAKOUT", default={})
        if not isinstance(m, dict):
            m = {}

        rows.append({
            "window": w,
            "trades": m.get("trades"),
            "win_rate": m.get("win_rate"),
            "pf": m.get("profit_factor"),
            "dd_pct": m.get("max_drawdown_pct"),
            "dd_yen": m.get("max_drawdown_yen"),
            "pnl": m.get("total_pnl"),
            "wins": m.get("wins"),
            "losses": m.get("losses"),
            "sum_win_yen": m.get("sum_win_yen"),
            "sum_loss_yen": m.get("sum_loss_yen"),
        })

    out["BREAKOUT"] = rows
    return out


def build_gate_bundle_for_template(state: AutoTradeDailyState):
    """
    gate の日本語理由（数値＋円入り）をテンプレ向けに整形する。
    runner.py が backtest['gate'] に入れている情報をそのまま使う（再計算しない）。

    返り値:
    {
      "final": {...},
      "BREAKOUT": {"gate_level": "...", "reasons": [...]},
      "final_reason_text": "...",
    }
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}

    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}
    gb = gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {}

    return {
        "final": {
            "gate_level": str(final.get("gate_level") or state.gate_level or "STOP"),
            "active": list(final.get("active") or []),
            "disabled": list(final.get("disabled") or []),
        },
        "BREAKOUT": {
            "gate_level": str(gb.get("gate_level") or "STOP"),
            "reasons": [str(x) for x in (gb.get("reasons") or []) if str(x).strip()],
        },
        "final_reason_text": (state.gate_reason or "").strip(),
    }


@login_required
def dashboard(request: HttpRequest):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    gate_bundle = build_gate_bundle_for_template(state)
    bt_rows_by_strategy = build_bt_rows_by_strategy_for_template(state)

    ctx = {
        "state": state,
        "gate": gate_bundle,
        "bt_rows_by_strategy": bt_rows_by_strategy,
        "initial_tab": "BREAKOUT",  # dashboard.js互換（タブ1つでもOK）
    }
    return render(request, "autotrade/dashboard.html", ctx)