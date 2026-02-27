"""
[FILE] autotrade/services/tuning/manual_backtest.py
[PATH] <project_root>/autotrade/services/tuning/manual_backtest.py

このファイルは何？
- 「実験室（TuningProfile）」の検証（BACKTEST）を実行するサービスです。
- ACTIVEは触らず、DRAFT Snapshot を作って、VWAP/BREAKOUT の詳細バックテストを回します。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from datetime import date as dt_date

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from autotrade.models import AutoTradeTuningProfile, AutoTradeSettingSnapshot
from autotrade.models_backtest import AutoTradeBacktestRunDetail

from autotrade.services.backtest.gate import judge_multi_window

from autotrade.services.backtest.engine_vwap_detail import run_vwap_detail
from autotrade.services.backtest.engine_breakout_detail import run_breakout_detail


def _pct_to_ratio(pct: float) -> float:
    try:
        return float(pct) / 100.0
    except Exception:
        return 0.0


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


def _safe_str(x: Any, default: str = "") -> str:
    try:
        s = str(x).strip()
        return s if s else str(default)
    except Exception:
        return str(default)


def _build_snapshot_dict_from_profile(profile: AutoTradeTuningProfile) -> Dict[str, Any]:
    """
    profile.params（自由）を、エンジンが読む snapshot dict に落とす。
    ここは “実験室の翻訳層”。
    """
    p = profile.params if isinstance(profile.params, dict) else {}
    v = p.get("VWAP") if isinstance(p.get("VWAP"), dict) else {}
    b = p.get("BREAKOUT") if isinstance(p.get("BREAKOUT"), dict) else {}

    # UIは%表記（0.25）→ ratio（0.0025）
    v_stop = _pct_to_ratio(_safe_float(v.get("stop_pct"), 0.25))
    b_stop = _pct_to_ratio(_safe_float(b.get("stop_pct"), 0.30))

    rr_v = _safe_float(v.get("rr"), 1.5)
    rr_b = _safe_float(b.get("rr"), 2.0)

    pullback_pct = _pct_to_ratio(_safe_float(v.get("pullback_pct"), 0.05))  # ratio

    lookback_bars = _safe_int(b.get("lookback_bars"), 6)

    max_hold_v = _safe_int(v.get("max_hold_min"), 30)
    max_hold_b = _safe_int(b.get("max_hold_min"), 30)

    # ★ 追加：日足フィルタ
    daily_filter = _safe_str(b.get("daily_filter"), "OFF").upper()
    if daily_filter not in ["OFF", "SMA"]:
        daily_filter = "OFF"

    sma_days = _safe_int(b.get("sma_days"), 20)
    if sma_days <= 0:
        sma_days = 20

    direction = _safe_str(b.get("direction"), "TREND_ONLY").upper()
    if direction not in ["TREND_ONLY", "BOTH"]:
        direction = "TREND_ONLY"

    snap = {
        "base_equity_yen": int(getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)),

        # detailエンジンが読むキー
        "vwap_stop_pct": float(v_stop),
        "breakout_stop_pct": float(b_stop),

        # ★ 追加：BREAKOUT側が参照するパラメータ
        "breakout_lookback_bars": int(lookback_bars),
        "breakout_max_hold_bars": int(max_hold_b // 5 + 1),
        "breakout_daily_filter": str(daily_filter),
        "breakout_sma_days": int(sma_days),
        "breakout_direction": str(direction),

        # 既存互換（古いエンジンが max_hold_bars しか見ない場合の保険）
        "max_hold_bars": int(max_hold_b // 5 + 1),

        # 実験室の“人間向け”原本も残す（UI表示に使える）
        "lab": {
            "VWAP": {
                "stop_pct_ui": float(_safe_float(v.get("stop_pct"), 0.25)),
                "rr": float(rr_v),
                "pullback_pct_ui": float(_safe_float(v.get("pullback_pct"), 0.05)),
                "pullback_pct_ratio": float(pullback_pct),
                "max_hold_min": int(max_hold_v),
            },
            "BREAKOUT": {
                "stop_pct_ui": float(_safe_float(b.get("stop_pct"), 0.30)),
                "rr": float(rr_b),
                "lookback_bars": int(lookback_bars),
                "max_hold_min": int(max_hold_b),

                "daily_filter": str(daily_filter),
                "sma_days": int(sma_days),
                "direction": str(direction),
            },
        },
    }

    return snap


# ---- ここから下は、あなたが貼ってくれた既存コードのまま（変更なし） ----

def _merge_gate_results(*, gate_vwap: Dict[str, Any], gate_breakout: Dict[str, Any]) -> Dict[str, Any]:
    lv_v = str((gate_vwap or {}).get("gate_level") or "STOP")
    lv_b = str((gate_breakout or {}).get("gate_level") or "STOP")

    active: List[str] = []
    disabled: List[str] = []

    if lv_v == "STOP":
        final = "STOP"
        disabled = ["VWAP", "BREAKOUT"]
    else:
        active.append("VWAP")
        if lv_b == "FULL":
            final = "FULL"
            active.append("BREAKOUT")
        else:
            final = "LIGHT"
            disabled.append("BREAKOUT")

    return {
        "gate_level": final,
        "active": active,
        "disabled": disabled,
        "gate_vwap": gate_vwap,
        "gate_breakout": gate_breakout,
    }


def _sort_key_exit_at(t: Dict[str, Any]):
    dt = t.get("exit_at")
    return dt or timezone.now()


def _summarize_trades(trades: List[Dict[str, Any]], base_equity_yen: int) -> Dict[str, Any]:
    trades = [t for t in (trades or []) if isinstance(t, dict)]
    trades_sorted = sorted(trades, key=_sort_key_exit_at)

    wins = 0
    losses = 0
    sum_win = 0
    sum_loss = 0
    total_pnl = 0

    equity = float(base_equity_yen)
    peak = float(base_equity_yen)
    max_dd_yen = 0.0

    for t in trades_sorted:
        pnl = t.get("pnl_yen")
        try:
            pnl_i = int(pnl)
        except Exception:
            pnl_i = 0

        total_pnl += pnl_i
        equity += float(pnl_i)

        if pnl_i >= 0:
            wins += 1
            sum_win += pnl_i
        else:
            losses += 1
            sum_loss += pnl_i

        if equity > peak:
            peak = equity
        dd = equity - peak
        if dd < max_dd_yen:
            max_dd_yen = dd

    trades_n = len(trades_sorted)
    win_rate = None
    if trades_n > 0:
        win_rate = round((wins / trades_n) * 100.0, 1)

    pf = None
    if sum_loss != 0:
        pf = round(float(sum_win) / max(abs(float(sum_loss)), 1e-9), 3)

    max_dd_pct = None
    if base_equity_yen > 0:
        max_dd_pct = round(abs(float(max_dd_yen)) / float(base_equity_yen), 4)

    return {
        "trades": trades_n,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "sum_win_yen": int(sum_win),
        "sum_loss_yen": int(sum_loss),
        "profit_factor": pf,
        "max_drawdown_yen": int(round(max_dd_yen)),
        "max_drawdown_pct": max_dd_pct,
        "total_pnl": int(total_pnl),
    }


def _collect_trades_for_strategy(
    *,
    strat: str,
    snap_dict: Dict[str, Any],
    ticker: str,
    window_days: int,
    rr: float,
    base_equity_yen: int,
) -> List[Dict[str, Any]]:
    try:
        if strat == "VWAP":
            out = run_vwap_detail(
                snapshot_dict=snap_dict,
                ticker=ticker,
                window_days=int(window_days),
                rr=float(rr),
                base_equity_yen=int(base_equity_yen),
            )
        else:
            out = run_breakout_detail(
                snapshot_dict=snap_dict,
                ticker=ticker,
                window_days=int(window_days),
                rr=float(rr),
                base_equity_yen=int(base_equity_yen),
            )
    except Exception:
        return []

    if not isinstance(out, dict):
        return []
    trades = out.get("trades")
    if not isinstance(trades, list):
        return []
    return [t for t in trades if isinstance(t, dict)]


@transaction.atomic
def run_backtest_for_tuning_profile(
    *,
    profile: AutoTradeTuningProfile,
    target_date: dt_date,
    picks: List[str],
    windows: Optional[List[int]] = None,
) -> Dict[str, Any]:
    if windows is None:
        windows = [20, 60]

    windows = [int(x) for x in (windows or [])]
    picks = [str(x).strip() for x in (picks or []) if str(x).strip()]

    snap_dict = _build_snapshot_dict_from_profile(profile)

    label = f"LAB {target_date} {profile.name}".strip()[:100]
    snap = AutoTradeSettingSnapshot.objects.create(
        user=profile.user,
        source_profile=profile,
        label=label,
        status="DRAFT",
        snapshot=snap_dict,
    )

    base_equity = int(snap_dict.get("base_equity_yen", 1_000_000))

    rr_vwap = float((snap_dict.get("lab") or {}).get("VWAP", {}).get("rr", 1.5))
    rr_breakout = float((snap_dict.get("lab") or {}).get("BREAKOUT", {}).get("rr", 2.0))

    metrics_by_window: Dict[int, Dict[str, Dict[str, Any]]] = {}

    for w in windows:
        metrics_by_window[int(w)] = {}

        for strat in ["VWAP", "BREAKOUT"]:
            rd = AutoTradeBacktestRunDetail.objects.create(
                user=profile.user,
                snapshot=snap,
                strategy=str(strat),
                window_days=int(w),
                start_date=target_date,
                end_date=target_date,
                trade_date=target_date,
            )

            all_trades: List[Dict[str, Any]] = []
            for ticker in picks:
                if strat == "VWAP":
                    ts = _collect_trades_for_strategy(
                        strat="VWAP",
                        snap_dict=snap_dict,
                        ticker=ticker,
                        window_days=int(w),
                        rr=rr_vwap,
                        base_equity_yen=base_equity,
                    )
                else:
                    ts = _collect_trades_for_strategy(
                        strat="BREAKOUT",
                        snap_dict=snap_dict,
                        ticker=ticker,
                        window_days=int(w),
                        rr=rr_breakout,
                        base_equity_yen=base_equity,
                    )
                all_trades.extend(ts)

            m = _summarize_trades(all_trades, base_equity_yen=base_equity)

            try:
                rd.note = f"manual_backtest trades={m.get('trades')}"
                rd.save(update_fields=["note"])
            except Exception:
                pass

            metrics_by_window[int(w)][str(strat)] = m

    metrics_vwap = {int(w): (metrics_by_window[int(w)].get("VWAP") or {}) for w in windows}
    metrics_breakout = {int(w): (metrics_by_window[int(w)].get("BREAKOUT") or {}) for w in windows}

    gate_vwap = judge_multi_window(metrics_vwap)
    gate_breakout = judge_multi_window(metrics_breakout)
    merged = _merge_gate_results(gate_vwap=gate_vwap, gate_breakout=gate_breakout)

    sdict = snap.snapshot if isinstance(snap.snapshot, dict) else {}
    sdict["manual_eval"] = {
        "evaluated_at": timezone.localtime(timezone.now()).isoformat(),
        "date": str(target_date),
        "windows": list(windows),
        "note": "manual_backtest_from_ui",
    }
    sdict["evidence"] = {
        "date": str(target_date),
        "base_equity_yen": int(base_equity),
        "gate": merged,
        "by_window": metrics_by_window,
    }
    snap.snapshot = sdict
    snap.save(update_fields=["snapshot"])

    return {
        "ok": True,
        "snapshot_id": int(snap.id),
        "gate": merged,
        "metrics_by_window": metrics_by_window,
    }