"""
[FILE] autotrade/services/backtest/gate.py
[PATH] <project_root>/autotrade/services/backtest/gate.py

このファイルは何？
- バックテスト結果（20/60/120）を見て、🟢🟡🔴を決める「採点基準（ゲート判定）」です。
- ここは “設定（Snapshot）” ではなく “合否判定ルール” 側です。
- なので、実験室でSnapshotをACTIVEにしても、ここは自動では変わりません。

今回の変更
- 攻め型（PF重視）として min_pf / min_trades / min_win_rate を採用
- 理由文を「/区切り」から完全撤去し、初心者が読みやすい“行”構成に変更
- 20日/60日/120日それぞれの判定が、UIで自然に読めるように整形
"""

from __future__ import annotations

from typing import Dict, Any, List, Tuple


# =========================================================
# 攻め型（PF重視）
# - BREAKOUTは勝率が高くなくてもPFで勝つ戦略なので、PFを強めに見る
# - ただし「回数が少ないPF」は信用できないので min_trades を上げる
# - DDは多少許容するが、破滅DDは止める
# =========================================================
GATE_THRESHOLDS = {
    "FULL": {
        "max_dd_pct": 0.03,      # 3%
        "min_pf": 1.10,          # ★強化
        "min_trades": 12,        # ★強化
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

    返り値:
      (level, reasons)
      level: "FULL" / "LIGHT" / "STOP"
      reasons: UIでそのまま箇条書き表示できる「読みやすい行」の配列
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

    # 勝率（%）: wins/trades が最も信頼できる
    win_rate = (wins / trades) if trades > 0 else 0.0

    def _base_lines() -> List[str]:
        return [
            f"最大落ち込み（DD）: -{_yen(dd_yen)}（-{_pct(dd)}）",
            f"PF（勝ち合計÷負け合計）: {pf:.2f}",
            f"取引回数: {trades}回（勝{wins} / 負{losses}）",
            f"勝率: {_pct(win_rate)}",
            f"純損益: {_yen(total_pnl)}",
        ]

    # 判定（FULL）
    full = GATE_THRESHOLDS["FULL"]
    ok_full = (
        dd <= float(full["max_dd_pct"])
        and pf >= float(full["min_pf"])
        and trades >= int(full["min_trades"])
        and win_rate >= float(full.get("min_win_rate", 0.0))
    )
    if ok_full:
        reasons = ["成績が安定しています（フル稼働OK）。"] + _base_lines()
        return "FULL", reasons

    # 判定（LIGHT）
    light = GATE_THRESHOLDS["LIGHT"]
    ok_light = (
        dd <= float(light["max_dd_pct"])
        and pf >= float(light["min_pf"])
        and trades >= int(light["min_trades"])
        and win_rate >= float(light.get("min_win_rate", 0.0))
    )

    # どこが足りないかを “読みやすい行” で列挙
    rs: List[str] = []
    rs.append("基準を満たしていない項目があります。")

    # DD
    if dd > float(light["max_dd_pct"]):
        rs.append(f"DDが大きすぎます（基準: -{_pct(light['max_dd_pct'])}以内）")
    # PF
    if pf < float(light["min_pf"]):
        rs.append(f"PFが弱いです（基準: {light['min_pf']:.2f}以上）")
    # trades
    if trades < int(light["min_trades"]):
        rs.append(f"取引回数が少ないです（基準: {light['min_trades']}回以上）")
    # win_rate
    if win_rate < float(light.get("min_win_rate", 0.0)):
        rs.append(f"勝率が低いです（基準: {_pct(light.get('min_win_rate', 0.0))}以上）")

    rs.extend(_base_lines())

    if ok_light:
        rs.append("軽稼働（LIGHT）なら稼働できます。")
        return "LIGHT", rs

    rs.append("現時点では停止（STOP）が安全です。")
    return "STOP", rs


def judge_multi_window(metrics_by_window: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """
    20/60/120 をまとめて判定して、最終の gate_level と理由を返す

    返り値:
    {
      "gate_level": "FULL"/"LIGHT"/"STOP",
      "reasons": [読みやすい行...],
      "detail": {20:"...", 60:"...", 120:"..."},
    }
    """
    detail: Dict[int, str] = {}
    reasons: List[str] = []

    per_window_lines: List[str] = []

    for w, m in sorted(metrics_by_window.items(), key=lambda x: x[0]):
        level, rs = _judge_single(m)
        detail[int(w)] = level

        # FULL 以外は「何がダメか」が重要なので詳細を展開
        if level != "FULL":
            per_window_lines.append(f"【{int(w)}日】判定: {level}")
            for line in rs:
                per_window_lines.append(f"  - {line}")

    # 最終判定（あなたの元ルール踏襲）
    if detail.get(20) == "STOP":
        final_level = "STOP"
        reasons.append("直近（20日）の成績が不安定です。")
        reasons.extend(per_window_lines)
    elif any(detail.get(w) == "STOP" for w in (60, 120)):
        final_level = "LIGHT"
        reasons.append("中長期の成績に不安があるため、軽稼働にします。")
        reasons.extend(per_window_lines)
    else:
        final_level = "FULL"
        reasons.append("すべての期間で安定しています。")

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