"""
[FILE] autotrade/services/backtest/gate.py
[PATH] <project_root>/autotrade/services/backtest/gate.py

このファイルは何？
- バックテスト結果（20/60/120）を見て、🟢🟡🔴を決める判定部品です。
- 旧 gate_service の役割をここに集約します。

初心者ポイント：
- 画面に出す「理由文」をここで作ります。
- 数字より理由を読めばOK、という思想です。
"""

from __future__ import annotations

from typing import Dict, Any, List, Tuple


GATE_THRESHOLDS = {
    "FULL": {
        "max_dd_pct": 0.02,     # 2%
        "min_pf": 1.05,
        "min_trades": 10,
    },
    "LIGHT": {
        "max_dd_pct": 0.04,     # 4%
        "min_pf": 1.00,
        "min_trades": 5,
    },
}


def _judge_single(metrics: Dict[str, Any]) -> Tuple[str, List[str]]:
    dd = float(metrics.get("max_drawdown_pct") or 1.0)
    pf = float(metrics.get("profit_factor") or 0.0)
    trades = int(metrics.get("trades") or 0)

    full = GATE_THRESHOLDS["FULL"]
    if dd <= full["max_dd_pct"] and pf >= full["min_pf"] and trades >= full["min_trades"]:
        return "FULL", ["成績が安定しており、問題ありません。"]

    light = GATE_THRESHOLDS["LIGHT"]
    light_reasons: List[str] = []
    if dd > light["max_dd_pct"]:
        light_reasons.append(f"資産の落ち込みが大きめです（最大 {dd*100:.1f}%）。")
    if pf < light["min_pf"]:
        light_reasons.append(f"利益と損失がほぼ同じです（PF={pf:.2f}）。")
    if trades < light["min_trades"]:
        light_reasons.append(f"取引回数が少なく、信頼性が低めです（{trades}回）。")

    if not light_reasons:
        return "LIGHT", ["成績は十分ではありませんが、大きな問題はありません。"]

    rs = list(light_reasons)
    rs.append("現時点では自動売買を止めるのが安全です。")
    return "STOP", rs


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

    if detail.get(20) == "STOP":
        final_level = "STOP"
        reasons.insert(0, "直近（20日）の成績が不安定です。")
    elif any(detail.get(w) == "STOP" for w in (60, 120)):
        final_level = "LIGHT"
        reasons.insert(0, "中長期の成績に不安があるため、慎重に運用します。")
    else:
        final_level = "FULL"
        reasons = ["すべての期間で安定した成績です。"]

    return {
        "gate_level": final_level,
        "reasons": reasons,
        "detail": detail,
    }


def gate_from_backtests(bt_by_window: Dict[int, Dict[str, Any]]) -> Tuple[str, str]:
    """
    旧 gate_service.gate_from_backtests の置き換え。

    入力：
      {
        20: {"metrics": {...}}  または {"max_drawdown_pct":..., ...}
        60: {"metrics": {...}}
        120:{...}
      }

    戻り値：
      (gate_level, reason_text)
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