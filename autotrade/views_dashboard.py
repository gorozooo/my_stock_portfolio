"""
[FILE] autotrade/views_dashboard.py
[PATH] <project_root>/autotrade/views_dashboard.py

このファイルは何？
- AutoTrade のダッシュボード表示（iPhone 1画面）用の View と整形関数です。
- state.backtest（runnerが保存した結果）を “再計算せず” テンプレ表示用に整形します。

今回の変更：
- 「判定理由」を gate.py の文章羅列ではなく、期間ごとの “結果カード” 形式に整形して返します。
- 合格ライン（GATE_THRESHOLDS）をテンプレで初心者向けに表示できるように渡します。
- ACTIVE Snapshot の “今動いている数値” も、専門用語だらけにならないようチップ文にして渡します。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import render

from .models import AutoTradeDailyState
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


def _pct_100(x01: Any) -> float:
    v = _safe_float(x01, 0.0)
    return float(v * 100.0)


def _level_by_thresholds(metrics: Dict[str, Any]) -> str:
    """
    metrics（PF/DD/N/勝率）から FULL/LIGHT/STOP を決める。
    ※gate.pyそのものは変更せず、表示用に “同じ基準” で判定する。
    """
    dd = _safe_float(metrics.get("max_drawdown_pct"), 1.0)
    pf = _safe_float(metrics.get("profit_factor"), 0.0)
    trades = _safe_int(metrics.get("trades"), 0)

    # win_rate は 0〜1 を期待。無ければ wins/trades で作る
    win_rate = metrics.get("win_rate")
    if win_rate is None:
        wins = _safe_int(metrics.get("wins"), 0)
        win_rate = (wins / trades) if trades > 0 else 0.0
    win_rate = _safe_float(win_rate, 0.0)

    full = GATE_THRESHOLDS.get("FULL", {})
    light = GATE_THRESHOLDS.get("LIGHT", {})

    def _ok(th: Dict[str, Any]) -> bool:
        if dd > _safe_float(th.get("max_dd_pct"), 0.0):
            return False
        if pf < _safe_float(th.get("min_pf"), 0.0):
            return False
        if trades < _safe_int(th.get("min_trades"), 0):
            return False

        # 任意項目（追加されている可能性に対応）
        if "min_win_rate" in th and th.get("min_win_rate") is not None:
            if win_rate < _safe_float(th.get("min_win_rate"), 0.0):
                return False
        return True

    if _ok(full):
        return "FULL"
    if _ok(light):
        return "LIGHT"
    return "STOP"


def _miss_points(metrics: Dict[str, Any], *, base: str = "LIGHT") -> List[str]:
    """
    未達ポイントを初心者向けの日本語にして返す（最大3つ）。
    base="LIGHT" を基準にする（現実的な安全ライン）。
    """
    th = (GATE_THRESHOLDS.get(base) or {}).copy()

    dd = _safe_float(metrics.get("max_drawdown_pct"), 1.0)
    pf = _safe_float(metrics.get("profit_factor"), 0.0)
    trades = _safe_int(metrics.get("trades"), 0)

    win_rate = metrics.get("win_rate")
    if win_rate is None:
        wins = _safe_int(metrics.get("wins"), 0)
        win_rate = (wins / trades) if trades > 0 else 0.0
    win_rate = _safe_float(win_rate, 0.0)

    out: List[str] = []

    max_dd = _safe_float(th.get("max_dd_pct"), 0.0)
    min_pf = _safe_float(th.get("min_pf"), 0.0)
    min_trades = _safe_int(th.get("min_trades"), 0)

    if pf < min_pf:
        out.append(f"PFが弱い（基準：{min_pf:.2f}以上）")
    if dd > max_dd:
        out.append(f"最大落ち込み（DD）が大きい（基準：{max_dd*100:.1f}%以内）")
    if trades < min_trades:
        out.append(f"取引回数が少ない（基準：{min_trades}回以上）")

    if "min_win_rate" in th and th.get("min_win_rate") is not None:
        min_wr = _safe_float(th.get("min_win_rate"), 0.0)
        if win_rate < min_wr:
            out.append(f"勝率が低い（基準：{min_wr*100:.1f}%以上）")

    return out[:3]


def _one_liner(level: str) -> str:
    if level == "FULL":
        return "安定しているので、通常稼働してOKです。"
    if level == "LIGHT":
        return "少し不安があるので、軽稼働（守り）ならOKです。"
    return "いまは不安定なので、停止が安全です。"


def build_result_cards_for_template(state: AutoTradeDailyState) -> List[Dict[str, Any]]:
    """
    state.backtest['by_window'][window]['BREAKOUT'] の metrics から
    期間ごとの “結果カード” を作る。
    """
    bt = state.backtest if isinstance(state.backtest, dict) else {}
    by_window = bt.get("by_window") if isinstance(bt.get("by_window"), dict) else {}

    # 監視期間（UI上は基本 [20,60]）
    meta = bt.get("meta") if isinstance(bt.get("meta"), dict) else {}
    windows = meta.get("windows")
    if not isinstance(windows, list) or not windows:
        windows = [20, 60]

    out: List[Dict[str, Any]] = []

    for w in windows:
        w = _safe_int(w, 0)
        if w <= 0:
            continue

        m = get_nested_dict(by_window, int(w), "BREAKOUT", default={})
        if not isinstance(m, dict):
            m = {}

        trades = _safe_int(m.get("trades"), 0)
        wins = _safe_int(m.get("wins"), 0)
        losses = _safe_int(m.get("losses"), 0)

        win_rate = m.get("win_rate")
        if win_rate is None:
            win_rate = (wins / trades) if trades > 0 else 0.0
        win_rate = _safe_float(win_rate, 0.0)

        pf = _safe_float(m.get("profit_factor"), 0.0)
        dd_pct = _safe_float(m.get("max_drawdown_pct"), 0.0)
        dd_yen = _safe_int(m.get("max_drawdown_yen"), 0)
        pnl_yen = _safe_int(m.get("total_pnl"), 0)

        sum_win_yen = _safe_int(m.get("sum_win_yen"), 0)
        sum_loss_yen = _safe_int(m.get("sum_loss_yen"), 0)
        sum_loss_abs_yen = abs(sum_loss_yen)

        level = _level_by_thresholds(m)

        out.append({
            "strategy": "BREAKOUT",
            "window": int(w),

            "level": str(level),
            "one_liner": _one_liner(str(level)),
            "miss_points": _miss_points(m, base="LIGHT"),

            "trades": int(trades),
            "wins": int(wins),
            "losses": int(losses),

            "win_rate_pct": round(_pct_100(win_rate), 1),
            "pf": f"{pf:.3f}" if pf else f"{pf:.2f}",
            "dd_pct": round(_pct_100(dd_pct), 1),
            "dd_yen": int(dd_yen),
            "pnl_yen": int(pnl_yen),

            "sum_win_yen": int(sum_win_yen),
            "sum_loss_abs_yen": int(sum_loss_abs_yen),
        })

    return out


def build_thresholds_for_template() -> Dict[str, Any]:
    """
    GATE_THRESHOLDS をテンプレ表示用に整形する。
    - min_win_rate が無い/ある どちらでも表示できるようにする
    """
    def _pack(name: str) -> Dict[str, Any]:
        th = (GATE_THRESHOLDS.get(name) or {}).copy()
        out: Dict[str, Any] = {
            "max_dd_pct": _safe_float(th.get("max_dd_pct"), 0.0) * 100.0,  # %表示用
            "min_pf": _safe_float(th.get("min_pf"), 0.0),
            "min_trades": _safe_int(th.get("min_trades"), 0),
            "min_win_rate": None,
            "min_win_rate_pct": None,
        }
        if "min_win_rate" in th and th.get("min_win_rate") is not None:
            out["min_win_rate"] = _safe_float(th.get("min_win_rate"), 0.0)
            out["min_win_rate_pct"] = round(_safe_float(th.get("min_win_rate"), 0.0) * 100.0, 1)
        return out

    return {
        "FULL": _pack("FULL"),
        "LIGHT": _pack("LIGHT"),
    }


def build_active_params_chips_for_template(state: AutoTradeDailyState) -> List[str]:
    """
    ACTIVE Snapshot（実運用パラメータ）を、初心者向けの“短いチップ文”にする。
    """
    chips: List[str] = []

    snap = getattr(state, "active_snapshot", None)
    sdict = {}
    if snap is not None:
        try:
            sdict = snap.snapshot if isinstance(snap.snapshot, dict) else {}
        except Exception:
            sdict = {}

    # 監視期間・RR・損切り幅・判定本数・最大保有・日足フィルタ
    windows = sdict.get("windows") or sdict.get("backtest_windows") or [20, 60]
    if isinstance(windows, list):
        chips.append(f"監視期間：{windows}")

    rr = sdict.get("rr_breakout") or sdict.get("RR_BREAKOUT") or sdict.get("rr") or None
    if rr is not None:
        try:
            chips.append(f"利確RR：{float(rr):.2f}")
        except Exception:
            chips.append(f"利確RR：{rr}")

    stop_pct = (
        sdict.get("stop_pct_breakout")
        or sdict.get("breakout_stop_pct")
        or get_nested_dict(sdict, "breakout", "stop_pct", default=None)
        or get_nested_dict(sdict, "params", "breakout", "stop_pct", default=None)
        or None
    )
    if stop_pct is not None:
        try:
            chips.append(f"損切り幅：{float(stop_pct)*100:.2f}%")
        except Exception:
            chips.append(f"損切り幅：{stop_pct}")

    lookback = (
        sdict.get("breakout_lookback_bars")
        or get_nested_dict(sdict, "breakout", "lookback_bars", default=None)
        or get_nested_dict(sdict, "params", "breakout", "lookback_bars", default=None)
        or None
    )
    if lookback is not None:
        try:
            chips.append(f"ブレイク判定本数：{int(lookback)}本（5分足）")
        except Exception:
            chips.append(f"ブレイク判定本数：{lookback}")

    max_hold_min = (
        sdict.get("breakout_max_hold_min")
        or get_nested_dict(sdict, "breakout", "max_hold_min", default=None)
        or get_nested_dict(sdict, "params", "breakout", "max_hold_min", default=None)
        or None
    )
    if max_hold_min is not None:
        try:
            chips.append(f"最大保有：{int(max_hold_min)}分")
        except Exception:
            chips.append(f"最大保有：{max_hold_min}分")

    daily_filter = (
        sdict.get("breakout_daily_filter")
        or get_nested_dict(sdict, "breakout", "daily_filter", default=None)
        or get_nested_dict(sdict, "params", "breakout", "daily_filter", default=None)
        or "OFF"
    )
    chips.append(f"日足フィルタ：{str(daily_filter).upper()}")

    if snap is not None:
        try:
            chips.append(f"active_snapshot_id：{snap.id}")
        except Exception:
            pass

    return chips


@login_required
def dashboard(request: HttpRequest):
    today = date.today()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    ctx = {
        "state": state,
        "result_cards": build_result_cards_for_template(state),
        "thresholds": build_thresholds_for_template(),
        "active_params_chips": build_active_params_chips_for_template(state),
    }
    return render(request, "autotrade/dashboard.html", ctx)