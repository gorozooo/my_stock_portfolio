"""
[FILE] autotrade/views_dashboard.py
[PATH] <project_root>/autotrade/views_dashboard.py

このファイルは何？
- AutoTrade のダッシュボード表示（iPhone 1画面）用の View と整形関数です。
- runner が state.backtest に詰めた結果（meta/by_window/gate）を “再計算せず” 表示用に整形します。

今回の変更
- バックテスト表示用整形（bt_rows_by_strategy）を廃止（画面から削除したため）
- 新設：合格ライン（gate.pyのGATE_THRESHOLDS）をテンプレに渡す
- 新設：現在の動いている数値（ACTIVE Snapshotの主要パラメータ）をテンプレに渡す
- 表示は初心者向けに “単位付き文字列” を作って渡す
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional, List

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import render
from django.utils import timezone

from .models import AutoTradeDailyState, AutoTradeSettingSnapshot
from autotrade.services.backtest.gate import GATE_THRESHOLDS


def _pct_str(x: Any, digits: int = 1) -> str:
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except Exception:
        return "-"


def _float_str(x: Any, digits: int = 2) -> str:
    try:
        return f"{float(x):.{digits}f}"
    except Exception:
        return "-"


def _int_str(x: Any) -> str:
    try:
        return f"{int(x)}"
    except Exception:
        return "-"


def build_gate_bundle_for_template(state: AutoTradeDailyState) -> Dict[str, Any]:
    """
    gate の日本語理由（数値＋円入り）をテンプレ向けに整形する。
    runner.py が backtest['gate'] に入れている情報をそのまま使う（再計算しない）。
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
    }


def build_thresholds_for_template() -> Dict[str, Any]:
    """
    gate.py の合格ライン（判定基準）を、テンプレで表示しやすい形に整形して返す。
    """
    def pack(d: Dict[str, Any]) -> Dict[str, str]:
        return {
            "max_dd_pct": _pct_str(d.get("max_dd_pct"), digits=1),
            "min_pf": _float_str(d.get("min_pf"), digits=2),
            "min_trades": _int_str(d.get("min_trades")),
            "min_win_rate": _pct_str(d.get("min_win_rate"), digits=1),
        }

    return {
        "FULL": pack(GATE_THRESHOLDS.get("FULL", {})),
        "LIGHT": pack(GATE_THRESHOLDS.get("LIGHT", {})),
    }


def _get_active_snapshot() -> Optional[AutoTradeSettingSnapshot]:
    return (
        AutoTradeSettingSnapshot.objects
        .filter(status="ACTIVE")
        .order_by("-created_at")
        .first()
    )


def build_active_params_for_template(state: AutoTradeDailyState) -> Optional[Dict[str, str]]:
    """
    ACTIVE Snapshot（実運用パラメータ）から、初心者向け表示用の文字列を作る。
    ※ “完全に正規化されたキー” ではないので、互換を見ながら拾う（安全側）。
    """
    snap = _get_active_snapshot()
    if not snap:
        return None

    sdict = snap.snapshot if isinstance(snap.snapshot, dict) else {}

    # windows（morning_prepareの実行条件に近いもの）
    windows = list(getattr(settings, "AUTOTRADE_BT_WINDOWS", [20, 60, 120]))
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    meta = bt.get("meta") if isinstance(bt.get("meta"), dict) else {}
    if isinstance(meta.get("windows"), list):
        windows = meta.get("windows")

    # rr_breakout（state.backtest.meta 優先）
    rr_breakout = meta.get("rr_breakout", None)
    if rr_breakout is None:
        rr_breakout = sdict.get("rr_breakout", None)
    if rr_breakout is None:
        rr_breakout = float(getattr(settings, "AUTOTRADE_RR_BREAKOUT", 2.0))

    # stop_pct（いくつか互換キーを見る）
    stop_pct = (
        sdict.get("breakout_stop_pct")
        or (sdict.get("BREAKOUT") or {}).get("stop_pct") if isinstance(sdict.get("BREAKOUT"), dict) else None
        or float(getattr(settings, "AUTOTRADE_BREAKOUT_STOP_PCT", 0.003))
    )

    # lookback bars
    lookback_bars = (
        sdict.get("breakout_lookback_bars")
        or (sdict.get("BREAKOUT") or {}).get("lookback_bars") if isinstance(sdict.get("BREAKOUT"), dict) else None
        or int(getattr(settings, "AUTOTRADE_BREAKOUT_LOOKBACK_BARS", 6))
    )

    # max hold（min 表記で見せる）
    max_hold_min = (
        sdict.get("breakout_max_hold_min")
        or (sdict.get("BREAKOUT") or {}).get("max_hold_min") if isinstance(sdict.get("BREAKOUT"), dict) else None
    )
    if max_hold_min is None:
        # bars があるなら min に変換（5分足前提）
        bars = (
            sdict.get("breakout_max_hold_bars")
            or (sdict.get("BREAKOUT") or {}).get("max_hold_bars") if isinstance(sdict.get("BREAKOUT"), dict) else None
        )
        if bars is not None:
            try:
                max_hold_min = int(bars) * 5
            except Exception:
                max_hold_min = None

    if max_hold_min is None:
        # それでも無ければ settings から bars を拾って min に変換
        bars = int(getattr(settings, "AUTOTRADE_BREAKOUT_MAX_HOLD_BARS", 20))
        max_hold_min = bars * 5

    # daily filter
    daily_filter = sdict.get("breakout_daily_filter", None)
    if daily_filter is None and isinstance(sdict.get("BREAKOUT"), dict):
        daily_filter = (sdict.get("BREAKOUT") or {}).get("daily_filter")
    daily_filter = str(daily_filter or "OFF").upper()

    if daily_filter == "SMA":
        sma_days = sdict.get("breakout_sma_days", None)
        if sma_days is None and isinstance(sdict.get("BREAKOUT"), dict):
            sma_days = (sdict.get("BREAKOUT") or {}).get("sma_days")
        direction = sdict.get("breakout_direction", None)
        if direction is None and isinstance(sdict.get("BREAKOUT"), dict):
            direction = (sdict.get("BREAKOUT") or {}).get("direction")
        direction = str(direction or "TREND_ONLY").upper()
        daily_label = f"SMA（{int(sma_days or 20)}日）/{direction}"
    else:
        daily_label = "OFF"

    return {
        "windows": f"{windows}",
        "rr_breakout": f"RR {float(rr_breakout):.2f}",
        "stop_pct": f"{_pct_str(float(stop_pct), digits=2)}",
        "lookback_bars": f"{int(lookback_bars)} 本（5分足）",
        "max_hold": f"{int(max_hold_min)} 分",
        "daily_filter": daily_label,
        "active_snapshot_id": f"{snap.id}",
    }


@login_required
def dashboard(request: HttpRequest):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    gate_bundle = build_gate_bundle_for_template(state)
    thresholds = build_thresholds_for_template()
    active_params = build_active_params_for_template(state)

    ctx = {
        "state": state,
        "gate": gate_bundle,
        "thresholds": thresholds,
        "active_params": active_params,
    }
    return render(request, "autotrade/dashboard.html", ctx)