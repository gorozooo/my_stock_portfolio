"""
[FILE] autotrade/views_dashboard.py
[PATH] <project_root>/autotrade/views_dashboard.py

このファイルは何？
- AutoTrade のダッシュボード表示（iPhone 1画面）用の View と整形関数です。

今回の変更：
- 「ACTIVE Snapshot のパラメータが唯一の真実」：
  - state.active_snapshot みたいな曖昧な属性参照は廃止
  - DBから status="ACTIVE" を毎回取得して表示する
  - チップ表示も “初心者向け要約チップ” を上に出し、詳細キーは折りたたみで出す前提のまま維持
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest
from django.shortcuts import render
from django.utils import timezone

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


def _flatten_dict(d: Any, *, prefix: str = "", max_depth: int = 3, depth: int = 0) -> List[Tuple[str, Any]]:
    """
    dictをフラット化して (keypath, value) のリストにする。
    - ネストが深すぎると読みづらいので max_depth まで
    """
    out: List[Tuple[str, Any]] = []
    if not isinstance(d, dict):
        return out

    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict) and depth < max_depth:
            out.extend(_flatten_dict(v, prefix=key, max_depth=max_depth, depth=depth + 1))
        else:
            out.append((key, v))
    return out


def _pretty_value(v: Any) -> str:
    """
    チップ用に value を短く人間向けにする
    """
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "ON" if v else "OFF"
    if isinstance(v, (int, float)):
        # 変に長い小数は丸める
        if isinstance(v, float):
            return f"{v:.6g}"
        return str(v)
    if isinstance(v, list):
        # リストは短く
        if len(v) <= 10:
            return str(v)
        return f"[{', '.join(map(str, v[:10]))}, ...]"
    s = str(v)
    if len(s) > 80:
        return s[:80] + "…"
    return s


def _get_active_snapshot(user) -> Optional[AutoTradeSettingSnapshot]:
    """
    ★唯一の真実：ACTIVE snapshot をDBから取得する
    """
    return (
        AutoTradeSettingSnapshot.objects
        .filter(user=user, status="ACTIVE")
        .order_by("-id")
        .first()
    )


def build_active_params_chips_for_template(*, user, active_snapshot: Optional[AutoTradeSettingSnapshot]) -> List[str]:
    """
    ACTIVE Snapshot（実運用パラメータ）を表示する。
    重要：表示も「DBのACTIVE」を唯一の真実にする。
    """
    chips: List[str] = []

    if active_snapshot is None:
        return ["ACTIVE Snapshot がありません（まだ昇格していない可能性）"]

    try:
        sdict = active_snapshot.snapshot if isinstance(active_snapshot.snapshot, dict) else {}
    except Exception:
        sdict = {}

    # 表示が爆発する巨大キーは除外
    exclude_prefixes = [
        "evidence",
        "auto_eval",
        "promote",
        "history",
        "debug",
        "raw",
        "logs",
    ]

    flat = _flatten_dict(sdict, max_depth=4)

    filtered: List[Tuple[str, Any]] = []
    for k, v in flat:
        k0 = str(k)
        if any(k0 == p or k0.startswith(p + ".") for p in exclude_prefixes):
            continue
        filtered.append((k0, v))

    priority_contains = [
        "windows",
        "rr",
        "stop_pct",
        "lookback",
        "max_hold",
        "daily_filter",
        "sma_days",
        "direction",
        "risk",
        "slippage",
        "base_equity",
        "universe",
    ]

    def _prio_key(k: str) -> Tuple[int, str]:
        for i, token in enumerate(priority_contains):
            if token.lower() in k.lower():
                return (i, k)
        return (999, k)

    filtered.sort(key=lambda kv: _prio_key(kv[0]))

    for k, v in filtered:
        chips.append(f"{k}：{_pretty_value(v)}")

    chips.append(f"active_snapshot_id：{active_snapshot.id}")
    chips.append(f"active_label：{active_snapshot.label}")

    if not chips:
        chips = ["ACTIVE Snapshot はありますが、表示できるキーがありません"]

    return chips


@login_required
def dashboard(request: HttpRequest):
    today = timezone.localdate()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    active_snapshot = _get_active_snapshot(request.user)

    ctx = {
        "state": state,
        "result_cards": build_result_cards_for_template(state),
        "thresholds": build_thresholds_for_template(),
        "active_params_chips": build_active_params_chips_for_template(user=request.user, active_snapshot=active_snapshot),
    }
    return render(request, "autotrade/dashboard.html", ctx)