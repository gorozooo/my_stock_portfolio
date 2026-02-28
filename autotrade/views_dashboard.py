"""
[FILE] autotrade/views_dashboard.py
[PATH] <project_root>/autotrade/views_dashboard.py

このファイルは何？
- AutoTrade のダッシュボード表示（iPhone 1画面）用の View と整形関数です。
- runner が state.backtest に詰めた結果（meta/by_window/gate）を “再計算せず” 表示用に整形します。

今回の変更：
- UI簡素化に合わせて、バックテスト表示用の行（bt_rows）を作らない（テンプレから削除済み）
- ★新設：合格ライン（gate.py の GATE_THRESHOLDS）をテンプレへ渡す
- ★新設：現在の動いている数値（ACTIVE Snapshot由来）をテンプレへ渡す
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import render
from django.conf import settings

from .models import AutoTradeDailyState, AutoTradeSettingSnapshot
from .views_utils import get_nested_dict

from autotrade.services.backtest.gate import GATE_THRESHOLDS


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _get_nested(d: Dict[str, Any], *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        if k in cur:
            cur = cur.get(k)
            continue
        ks = str(k)
        if ks in cur:
            cur = cur.get(ks)
            continue
        return default
    return cur if cur is not None else default


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


def build_thresholds_for_template() -> Dict[str, Any]:
    """
    gate.py の合格ライン（GATE_THRESHOLDS）をテンプレ表示用に整形。
    min_win_rate が無い構成でも落ちないようにする。
    """
    full = dict(GATE_THRESHOLDS.get("FULL") or {})
    light = dict(GATE_THRESHOLDS.get("LIGHT") or {})

    def _fmt_pct(v):
        try:
            return f"{float(v) * 100:.1f}%"
        except Exception:
            return "-"

    def _fmt_float(v):
        try:
            return f"{float(v):.2f}"
        except Exception:
            return "-"

    def _fmt_int(v):
        try:
            return f"{int(v)}"
        except Exception:
            return "-"

    out = {
        "FULL": {
            "max_dd_pct": _fmt_pct(full.get("max_dd_pct")),
            "min_pf": _fmt_float(full.get("min_pf")),
            "min_trades": _fmt_int(full.get("min_trades")),
            "min_win_rate": _fmt_pct(full.get("min_win_rate")) if full.get("min_win_rate") is not None else None,
        },
        "LIGHT": {
            "max_dd_pct": _fmt_pct(light.get("max_dd_pct")),
            "min_pf": _fmt_float(light.get("min_pf")),
            "min_trades": _fmt_int(light.get("min_trades")),
            "min_win_rate": _fmt_pct(light.get("min_win_rate")) if light.get("min_win_rate") is not None else None,
        },
    }
    return out


def build_current_params_for_template(state: AutoTradeDailyState) -> Dict[str, Any]:
    """
    「現在の動いている数値（今日の設定）」を作る。
    優先順位：
      1) ACTIVE Snapshot（実運用の正）
      2) state.backtest['meta']（runnerが保存していれば）
      3) settings のデフォルト
    """
    active = (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )

    snap = active.snapshot if (active and isinstance(active.snapshot, dict)) else {}
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    meta = bt.get("meta") if isinstance(bt.get("meta"), dict) else {}

    # windows
    windows = meta.get("windows") or _get_nested(snap, "windows") or getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60, 120])
    try:
        windows_txt = "[" + ", ".join(str(int(x)) for x in list(windows)) + "]"
    except Exception:
        windows_txt = str(windows)

    # rr_breakout
    rr_breakout = meta.get("rr_breakout")
    if rr_breakout is None:
        rr_breakout = snap.get("rr_breakout") or _get_nested(snap, "BREAKOUT", "rr") or _get_nested(snap, "breakout", "rr") or getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0)
    rr_breakout_txt = f"{_safe_float(rr_breakout, 0.0):.2f}" if rr_breakout is not None else "-"

    # stop_pct（0.003 = 0.30% なので %表記にする）
    stop_pct = (
        snap.get("stop_pct_breakout")
        or snap.get("breakout_stop_pct")
        or _get_nested(snap, "breakout", "stop_pct")
        or _get_nested(snap, "params", "breakout", "stop_pct")
        or _get_nested(snap, "BREAKOUT", "stop_pct")
        or getattr(settings, "AUTOTRADE_BREAKOUT_STOP_PCT", 0.003)
    )
    try:
        stop_pct_txt = f"{float(stop_pct) * 100:.2f}%"
    except Exception:
        stop_pct_txt = "-"

    # lookback_bars
    lookback_bars = (
        snap.get("breakout_lookback_bars")
        or _get_nested(snap, "breakout", "lookback_bars")
        or _get_nested(snap, "params", "breakout", "lookback_bars")
        or _get_nested(snap, "BREAKOUT", "lookback_bars")
        or getattr(settings, "AUTOTRADE_BREAKOUT_LOOKBACK_BARS", 6)
    )
    lookback_bars_txt = str(_safe_int(lookback_bars, 6))

    # max_hold_min（bars直指定があれば min換算）
    max_hold_min = (
        snap.get("breakout_max_hold_min")
        or _get_nested(snap, "breakout", "max_hold_min")
        or _get_nested(snap, "params", "breakout", "max_hold_min")
        or _get_nested(snap, "BREAKOUT", "max_hold_min")
    )
    if max_hold_min is None:
        bars = (
            snap.get("breakout_max_hold_bars")
            or snap.get("max_hold_bars")
            or _get_nested(snap, "breakout", "max_hold_bars")
            or _get_nested(snap, "params", "breakout", "max_hold_bars")
            or _get_nested(snap, "BREAKOUT", "max_hold_bars")
            or getattr(settings, "AUTOTRADE_BREAKOUT_MAX_HOLD_BARS", 20)
        )
        max_hold_min = _safe_int(bars, 20) * 5
    max_hold_min_txt = str(_safe_int(max_hold_min, 30))

    # daily filter params
    daily_filter = (
        snap.get("breakout_daily_filter")
        or _get_nested(snap, "breakout", "daily_filter")
        or _get_nested(snap, "params", "breakout", "daily_filter")
        or _get_nested(snap, "BREAKOUT", "daily_filter")
        or "OFF"
    )
    daily_filter = str(daily_filter).upper() if daily_filter else "OFF"
    if daily_filter not in ["OFF", "SMA"]:
        daily_filter = "OFF"

    sma_days = (
        snap.get("breakout_sma_days")
        or _get_nested(snap, "breakout", "sma_days")
        or _get_nested(snap, "params", "breakout", "sma_days")
        or _get_nested(snap, "BREAKOUT", "sma_days")
        or 20
    )
    sma_days_txt = str(_safe_int(sma_days, 20))

    direction = (
        snap.get("breakout_direction")
        or _get_nested(snap, "breakout", "direction")
        or _get_nested(snap, "params", "breakout", "direction")
        or _get_nested(snap, "BREAKOUT", "direction")
        or "TREND_ONLY"
    )
    direction = str(direction).upper() if direction else "TREND_ONLY"
    if direction not in ["TREND_ONLY", "BOTH"]:
        direction = "TREND_ONLY"

    return {
        "active_snapshot_id": int(active.id) if active else None,
        "windows": windows_txt,
        "rr_breakout": rr_breakout_txt,
        "stop_pct": stop_pct_txt,
        "lookback_bars": lookback_bars_txt,
        "max_hold_min": max_hold_min_txt,
        "daily_filter": daily_filter,
        "sma_days": sma_days_txt,
        "direction": direction,
    }


@login_required
def dashboard(request: HttpRequest):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    gate_bundle = build_gate_bundle_for_template(state)
    thresholds = build_thresholds_for_template()
    current_params = build_current_params_for_template(state)

    ctx = {
        "state": state,
        "gate": gate_bundle,
        "thresholds": thresholds,
        "current_params": current_params,
        "initial_tab": "BREAKOUT",  # dashboard.js互換（タブ1つでもOK）
    }
    return render(request, "autotrade/dashboard.html", ctx)