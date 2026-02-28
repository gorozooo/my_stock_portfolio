"""
[FILE] autotrade/views_dashboard.py
[PATH] <project_root>/autotrade/views_dashboard.py

このファイルは何？
- AutoTrade のダッシュボード表示（iPhone 1画面）用の View と整形関数です。
- runner が state.backtest に詰めた結果（meta/by_window/gate）を “再計算せず” 表示用に整形します。

今回の変更点（初心者UI向け）：
- gate.BREAKOUT.reasons は “verbose（期間別の箇条書き）” を優先して表示
- 合格ライン（GATE_THRESHOLDS）をテンプレへ渡す（/区切り廃止）
- 今日の設定（ACTIVE snapshotの主要パラメータ）をテンプレへ渡す
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import render

from .models import AutoTradeDailyState, AutoTradeSettingSnapshot
from .views_utils import get_nested_dict


def _safe_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _safe_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def build_gate_bundle_for_template(state: AutoTradeDailyState):
    """
    gate の日本語理由（数値＋円入り）をテンプレ向けに整形する。
    runner.py が backtest['gate'] に入れている情報をそのまま使う（再計算しない）。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    gate = bt.get("gate") if isinstance(bt.get("gate"), dict) else {}

    final = gate.get("final") if isinstance(gate.get("final"), dict) else {}
    gb = gate.get("BREAKOUT") if isinstance(gate.get("BREAKOUT"), dict) else {}

    # gate.py 側で "reasons_verbose" が入っていれば、それを優先
    # （runner側がまだ未対応でも落ちない）
    reasons = gb.get("reasons_verbose")
    if not isinstance(reasons, list):
        reasons = gb.get("reasons")
    reasons = [str(x) for x in (reasons or [])]

    return {
        "final": {
            "gate_level": str(final.get("gate_level") or state.gate_level or "STOP"),
            "active": list(final.get("active") or []),
            "disabled": list(final.get("disabled") or []),
        },
        "BREAKOUT": {
            "gate_level": str(gb.get("gate_level") or "STOP"),
            "reasons": [str(x) for x in reasons],
        },
    }


def build_thresholds_for_template() -> Dict[str, List[str]]:
    """
    gate.py の GATE_THRESHOLDS を “初心者向けの文章” にして返す。
    """
    try:
        from autotrade.services.backtest.gate import GATE_THRESHOLDS
    except Exception:
        GATE_THRESHOLDS = {}

    out: Dict[str, List[str]] = {"FULL": [], "LIGHT": []}

    for lv in ["FULL", "LIGHT"]:
        d = (GATE_THRESHOLDS.get(lv) or {})
        pf = _safe_float(d.get("min_pf"), 0.0)
        dd = _safe_float(d.get("max_dd_pct"), 1.0)
        tr = _safe_int(d.get("min_trades"), 0)
        wr = d.get("min_win_rate", None)

        out[lv] = [
            f"PF：{pf:.2f} 以上（利益が負けを上回る強さ）",
            f"最大落ち込み（DD）：{dd*100:.1f}% 以内（落ち込みが大きすぎない）",
            f"取引回数：{tr} 回以上（サンプルが少なすぎない）",
        ]
        if wr is not None:
            out[lv].append(f"勝率：{float(wr)*100:.1f}% 以上（最低限の安定感）")

    return out


def build_active_settings_for_template(user) -> List[str]:
    """
    ACTIVE Snapshot（実運用）の主要パラメータをチップ表示用に整形。
    """
    snap = (
        AutoTradeSettingSnapshot.objects
        .filter(user=user, status="ACTIVE")
        .order_by("-id")
        .first()
    )
    if not snap:
        return ["ACTIVE snapshot：未設定"]

    sdict = snap.snapshot if isinstance(snap.snapshot, dict) else {}

    windows = sdict.get("windows")
    if not isinstance(windows, list):
        # settings由来や互換で持ってないケース
        windows = [20, 60]

    rr_breakout = sdict.get("rr_breakout")
    if rr_breakout is None:
        # 互換（tune配下にある場合）
        tune = sdict.get("tune") if isinstance(sdict.get("tune"), dict) else {}
        rr_breakout = tune.get("rr_breakout")
    rr_breakout = _safe_float(rr_breakout, 2.0)

    # breakout params（複数キー互換：engine_breakoutが拾うのと揃える）
    stop_pct = sdict.get("breakout_stop_pct")
    if stop_pct is None:
        stop_pct = get_nested_dict(sdict, "BREAKOUT", "stop_pct", default=None)
    stop_pct = _safe_float(stop_pct, 0.003)

    lookback = sdict.get("breakout_lookback_bars")
    if lookback is None:
        lookback = get_nested_dict(sdict, "BREAKOUT", "lookback_bars", default=None)
    lookback = _safe_int(lookback, 6)

    max_hold_min = sdict.get("breakout_max_hold_min")
    if max_hold_min is None:
        max_hold_min = get_nested_dict(sdict, "BREAKOUT", "max_hold_min", default=None)
    max_hold_min = _safe_int(max_hold_min, 30)

    daily_filter = sdict.get("breakout_daily_filter") or get_nested_dict(sdict, "BREAKOUT", "daily_filter", default="OFF")
    daily_filter = str(daily_filter or "OFF").upper()

    return [
        f"監視期間：{windows}",
        f"利確RR：{rr_breakout:.2f}",
        f"損切り幅：{stop_pct*100:.2f}%",
        f"ブレイク判定本数：{lookback} 本（5分足）",
        f"最大保有：{max_hold_min} 分",
        f"日足フィルタ：{daily_filter}",
        f"active_snapshot_id：{snap.id}",
    ]


@login_required
def dashboard(request: HttpRequest):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    gate_bundle = build_gate_bundle_for_template(state)
    thresholds = build_thresholds_for_template()
    active_settings = build_active_settings_for_template(request.user)

    ctx = {
        "state": state,
        "gate": gate_bundle,
        "thresholds": thresholds,
        "active_settings": active_settings,
    }
    return render(request, "autotrade/dashboard.html", ctx)