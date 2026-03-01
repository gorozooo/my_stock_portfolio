# =========================================================
# [FILE] autotrade/views_dashboard.py
# [PATH] <project_root>/autotrade/views_dashboard.py
#
# このファイルは何？
# - AutoTrade のダッシュボード表示（iPhone 1画面）用の View と整形関数です。
# - state.backtest（runnerが保存した結果）を “再計算せず” テンプレ表示用に整形します。
#
# 今回の変更：
# - dashboard() で ACTIVE Snapshot を正しく取得し、state.active_snapshot にセットする
# - ACTIVE Snapshot の表示を「初心者向けの要約」＋「詳細（デバッグ）」の2段にする
# - 「判定理由 / 合格ライン / 今日の対象銘柄」を折りたたみ表示できるよう、テンプレ側に渡す形は維持
# =========================================================

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Tuple

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
        if isinstance(v, float):
            return f"{v:.6g}"
        return str(v)
    if isinstance(v, list):
        if len(v) <= 10:
            return str(v)
        return f"[{', '.join(map(str, v[:10]))}, ...]"
    s = str(v)
    if len(s) > 80:
        return s[:80] + "…"
    return s


def _fmt_pct_from_ratio(x: Any) -> str:
    """
    0.004 -> 0.40% のように表示
    """
    v = _safe_float(x, 0.0)
    return f"{v*100.0:.2f}%"


def build_active_params_for_template(state: AutoTradeDailyState) -> Dict[str, List[str]]:
    """
    ACTIVE Snapshot（実運用パラメータ）を
    - summary（初心者向け）
    - debug（全部/デバッグ）
    の2段で返す。
    """
    snap = getattr(state, "active_snapshot", None)
    if snap is None:
        return {
            "summary": ["ACTIVE Snapshot がありません（まだ昇格していない可能性）"],
            "debug": [],
        }

    try:
        sdict = snap.snapshot if isinstance(snap.snapshot, dict) else {}
    except Exception:
        sdict = {}

    # --------
    # 初心者向け “要約”
    # --------
    summary: List[str] = []

    # id/label
    label = getattr(snap, "label", None) or "-"
    summary.append(f"採用中の設定：ID {snap.id}（{label}）")

    # よく出る構造に寄せて取り出す
    manual_eval = sdict.get("manual_eval") if isinstance(sdict.get("manual_eval"), dict) else {}
    lab = sdict.get("lab") if isinstance(sdict.get("lab"), dict) else {}
    bo = lab.get("BREAKOUT") if isinstance(lab.get("BREAKOUT"), dict) else {}

    # windows
    windows = manual_eval.get("windows")
    if not isinstance(windows, list) or not windows:
        windows = sdict.get("windows") if isinstance(sdict.get("windows"), list) else None
    if windows:
        summary.append(f"判定に使う期間：{windows}")

    # base_equity
    base_eq = sdict.get("base_equity_yen", None)
    if base_eq is None:
        base_eq = manual_eval.get("base_equity_yen", None)
    if base_eq is not None:
        summary.append(f"基準資金：{_safe_int(base_eq, 0):,}円".replace(",", ""))  # intcommaはテンプレ側に任せないので軽く整形

    # RR
    rr = bo.get("rr", None)
    if rr is None:
        rr = sdict.get("rr_breakout", None)
    if rr is None:
        rr = manual_eval.get("rr_breakout", None)
    if rr is not None:
        summary.append(f"利確RR：{_safe_float(rr, 0.0):.2f}（利確幅＝損切幅×RR）")

    # stop_pct
    # 例：breakout_stop_pct: 0.004（=0.40%）
    stop_ratio = sdict.get("breakout_stop_pct", None)
    stop_ui = bo.get("stop_pct_ui", None)

    if stop_ratio is not None:
        summary.append(f"損切り幅：{_fmt_pct_from_ratio(stop_ratio)}")
    elif stop_ui is not None:
        summary.append(f"損切り幅：{_safe_float(stop_ui, 0.0):.2f}%")

    # lookback_bars
    lb = bo.get("lookback_bars", None)
    if lb is not None:
        summary.append(f"ブレイク判定：直近 {int(_safe_int(lb, 0))} 本（5分足）")

    # max_hold_min / bars
    mh_min = bo.get("max_hold_min", None)
    mh_bars = sdict.get("max_hold_bars", None)
    if mh_min is not None:
        summary.append(f"最大保有：{int(_safe_int(mh_min, 0))} 分")
    if mh_bars is not None:
        summary.append(f"最大保有（本数換算）：{int(_safe_int(mh_bars, 0))} 本（5分足）")

    # 日足フィルタ系（あれば）
    daily_filter = bo.get("daily_filter", None)
    sma_days = bo.get("sma_days", None)
    direction = bo.get("direction", None)

    if daily_filter is not None:
        if str(daily_filter).upper() == "OFF":
            summary.append("日足フィルタ：OFF（使わない）")
        else:
            summary.append(f"日足フィルタ：{daily_filter}")
    if sma_days is not None:
        summary.append(f"SMA日数：{int(_safe_int(sma_days, 0))} 日")
    if direction is not None:
        if str(direction).upper() == "TREND_ONLY":
            summary.append("方向制限：トレンド方向だけ（TREND_ONLY）")
        else:
            summary.append(f"方向制限：{direction}")

    # manual_eval info（あれば）
    m_date = manual_eval.get("date", None)
    if m_date:
        summary.append(f"評価日：{m_date}")
    note = manual_eval.get("note", None)
    if note:
        if str(note) == "manual_backtest_from_ui":
            summary.append("評価方法：UIから手動バックテスト")
        else:
            summary.append(f"評価方法メモ：{note}")

    # --------
    # デバッグ用 “全部”（フラット表示）
    # --------
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
        "manual_eval.windows",
        "lab.BREAKOUT.rr",
        "breakout_stop_pct",
        "lab.BREAKOUT.stop_pct_ui",
        "lab.BREAKOUT.lookback_bars",
        "lab.BREAKOUT.max_hold_min",
        "max_hold_bars",
        "base_equity_yen",
        "manual_eval.date",
        "manual_eval.evaluated_at",
    ]

    def _prio_key(k: str) -> Tuple[int, str]:
        for i, token in enumerate(priority_contains):
            if token.lower() in k.lower():
                return (i, k)
        return (999, k)

    filtered.sort(key=lambda kv: _prio_key(kv[0]))

    debug: List[str] = []
    for k, v in filtered:
        debug.append(f"{k}：{_pretty_value(v)}")

    # 最後にID/label
    debug.append(f"active_snapshot_id：{snap.id}")
    debug.append(f"active_label：{label}")

    return {
        "summary": summary[:20],  # 長くなりすぎ防止
        "debug": debug[:200],     # iPhoneで死なない上限（必要なら増やせる）
    }


def _get_current_active_snapshot(user) -> AutoTradeSettingSnapshot | None:
    return (
        AutoTradeSettingSnapshot.objects
        .filter(user=user, status="ACTIVE")
        .order_by("-id")
        .first()
    )


@login_required
def dashboard(request: HttpRequest):
    # JSTの日付を優先（モデルの date は DateField なので localdate が安全）
    today = timezone.localdate()
    state, _ = AutoTradeDailyState.objects.get_or_create(date=today)

    # ★ ここが重要：ACTIVE Snapshot を state に載せる
    active = _get_current_active_snapshot(request.user)
    state.active_snapshot = active  # DB保存はしない、表示用の一時属性

    active_params = build_active_params_for_template(state)

    ctx = {
        "state": state,
        "result_cards": build_result_cards_for_template(state),
        "thresholds": build_thresholds_for_template(),
        "active_params": active_params,  # summary/debug の2段
    }
    return render(request, "autotrade/dashboard.html", ctx)