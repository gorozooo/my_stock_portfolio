# =========================================================
# [FILE] autotrade/services/backtest/gate.py
# [PATH] <project_root>/autotrade/services/backtest/gate.py
#
# このファイルは何？
# - バックテスト結果（20/60/120）を見て、🟢🟡🔴を決める判定部品です。
# - 画面に出す「理由文」もここで作ります。
#
# 今回の変更（攻め型 / BREAKOUT最適化）：
# - FULL の条件を「PF重視」に強化（PFしきい値を引き上げ）
# - 取引回数と勝率の最低ラインも追加（薄い成績での誤判定を減らす）
# - DDは多少許容（ただし破滅しない上限は守る）
# =========================================================

from __future__ import annotations

from typing import Dict, Any, List, Tuple


# 攻め型（PF重視）
# - BREAKOUTは勝率が高くなくてもPFで勝つ戦略なので、PFを強めに見る
# - ただし「回数が少ないPF」は信用できないので min_trades を上げる
# - DDは多少許容するが、破滅DDは止める
GATE_THRESHOLDS = {
    "FULL": {
        "max_dd_pct": 0.03,      # 3%
        "min_pf": 1.10,          # ★強化
        "min_trades": 12,        # ★強化（薄い成績での誤判定を避ける）
        "min_win_rate": 0.42,    # ★追加（42%）
    },
    "LIGHT": {
        "max_dd_pct": 0.05,      # 5%
        "min_pf": 1.04,          # ★少し強化
        "min_trades": 8,         # ★少し強化
        "min_win_rate": 0.38,    # ★追加（38%）
    },
}


def _yen(x: Any) -> str:
    try:
        return f"{int(x):,}円"
    except Exception:
        return "0円"


def _pct(x: Any) -> str:
    try:
        return f"{float(x) * 100:.1f}%"
    except Exception:
        return "0.0%"


def _judge_single(metrics: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    metrics には summarize_executions の戻り値（trades/PF/DD/円分解）が入る想定。
    """
    dd = float(metrics.get("max_drawdown_pct") or 1.0)
    pf = float(metrics.get("profit_factor") or 0.0)
    trades = int(metrics.get("trades") or 0)

    dd_yen = int(metrics.get("max_drawdown_yen") or 0)
    sum_win = int(metrics.get("sum_win_yen") or 0)
    sum_loss = int(metrics.get("sum_loss_yen") or 0)  # 負の値の想定
    total_pnl = int(metrics.get("total_pnl") or 0)
    wins = int(metrics.get("wins") or 0)
    losses = int(metrics.get("losses") or 0)

    # 勝率（%）
    # - 基本は wins/trades
    # - winsが無い互換ケースは win_rate を拾う
    win_rate = (wins / trades) if trades > 0 and wins >= 0 else float(metrics.get("win_rate") or 0.0)
    win_rate_pct = win_rate * 100.0

    full = GATE_THRESHOLDS["FULL"]
    if (
        dd <= full["max_dd_pct"]
        and pf >= full["min_pf"]
        and trades >= full["min_trades"]
        and win_rate >= full["min_win_rate"]
    ):
        reasons = [
            "攻め型基準で見ても成績が良く、通常稼働できます。",
            f"最大落ち込み：-{_yen(dd_yen)}（-{_pct(dd)}）",
            f"勝ち合計：+{_yen(sum_win)} / 負け合計：-{_yen(abs(sum_loss))}（PF={pf:.2f}）",
            f"純損益：{_yen(total_pnl)}（勝{wins} / 負{losses}、勝率{win_rate_pct:.1f}%、合計{trades}回）",
        ]
        return "FULL", reasons

    light = GATE_THRESHOLDS["LIGHT"]
    rs: List[str] = []

    # DD
    if dd > light["max_dd_pct"]:
        rs.append(f"最大落ち込み：-{_yen(dd_yen)}（-{_pct(dd)}）が大きめです。")
    else:
        rs.append(f"最大落ち込み：-{_yen(dd_yen)}（-{_pct(dd)}）")

    # PF
    if pf < light["min_pf"]:
        rs.append(f"勝ち合計：+{_yen(sum_win)} / 負け合計：-{_yen(abs(sum_loss))} → PFが弱めです（PF={pf:.2f}）。")
    else:
        rs.append(f"勝ち合計：+{_yen(sum_win)} / 負け合計：-{_yen(abs(sum_loss))}（PF={pf:.2f}）")

    # Trades
    if trades < light["min_trades"]:
        rs.append(f"取引回数：{trades}回（最低{light['min_trades']}回必要）で信頼性が低めです。")
    else:
        rs.append(f"取引回数：{trades}回（勝{wins} / 負{losses}、勝率{win_rate_pct:.1f}%）")

    # Win rate
    if win_rate < light["min_win_rate"]:
        rs.append(f"勝率：{win_rate_pct:.1f}%（最低{light['min_win_rate'] * 100:.1f}%）でやや不安です。")

    rs.append(f"純損益：{_yen(total_pnl)}")

    if (
        dd <= light["max_dd_pct"]
        and pf >= light["min_pf"]
        and trades >= light["min_trades"]
        and win_rate >= light["min_win_rate"]
    ):
        return "LIGHT", rs + ["攻め型だとFULL未満ですが、慎重運用（LIGHT）なら稼働可能です。"]

    return "STOP", rs + ["現時点では自動売買を止めるのが安全です。"]


def judge_multi_window(metrics_by_window: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """
    20/60/120 をまとめて判定して、最終の gate_level と理由を返す
    """
    detail: Dict[int, str] = {}
    reasons: List[str] = []

    for w, m in sorted(metrics_by_window.items(), key=lambda x: x[0]):
        level, rs = _judge_single(m)
        detail[int(w)] = level
        if level != "FULL":
            reasons.append(f"{w}日検証：{level}（" + " / ".join(rs) + "）")

    # 最終判定ロジック（攻め型でも “20日STOPは即STOP” は維持）
    if detail.get(20) == "STOP":
        final_level = "STOP"
        reasons.insert(0, "直近（20日）の成績が不安定です。")
    elif any(detail.get(w) == "STOP" for w in (60, 120)):
        final_level = "LIGHT"
        reasons.insert(0, "中長期の成績に不安があるため、慎重に運用します。")
    else:
        final_level = "FULL"
        reasons = ["すべての期間で攻め型基準でも安定した成績です。"]

    return {
        "gate_level": final_level,
        "reasons": reasons,
        "detail": detail,
    }


def gate_from_backtests(bt_by_window: Dict[int, Dict[str, Any]]) -> Tuple[str, str]:
    """
    旧 gate_service.gate_from_backtests の置き換え。
    """
    metrics_by_window: Dict[int, Dict[str, Any]] = {}
    for w in (20, 60, 120):
        d = (bt_by_window or {}).get(int(w)) or {}
        m = d.get("metrics") if isinstance(d, dict) else None
        metrics_by_window[int(w)] = (m if isinstance(m, dict) else d) or {}

    res = judge_multi_window(metrics_by_window)
    gate_level = str(res.get("gate_level") or "STOP")
    reason_text = "\n".join(res.get("reasons") or [])
    return gate_level, reason_text