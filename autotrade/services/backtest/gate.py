"""
[FILE] autotrade/services/backtest/gate.py
[PATH] <project_root>/autotrade/services/backtest/gate.py

このファイルは何？
- バックテスト結果（metrics）を見て、
  「今日は自動売買していいか？」を 3段階（🟢🟡🔴）で判定します。

3段階ゲートとは？
🟢 FULL  ：通常どおり稼働してOK
🟡 LIGHT ：条件を軽くして慎重に稼働
🔴 STOP  ：今日は自動売買しない

初心者ポイント：
- 数字そのものより「なぜそう判断されたか」を読む場所です。
- 画面には、ここで作った日本語理由文をそのまま出せます。
"""

from typing import Dict, Tuple, List


# =========================================================
# 判定に使う基準値（まずは固定・将来拡張OK）
# =========================================================
# ※ あなたが決めた 3段階ゲート仕様をそのまま反映
GATE_THRESHOLDS = {
    "FULL": {
        "max_dd_pct": 0.02,     # 最大ドローダウン 2%以内
        "min_pf": 1.05,         # PF 1.05以上
        "min_trades": 10,       # 取引回数が少なすぎない
    },
    "LIGHT": {
        "max_dd_pct": 0.04,     # 4%以内
        "min_pf": 1.00,         # トントン以上
        "min_trades": 5,
    },
}


# =========================================================
# 単一期間（20日など）の判定
# =========================================================
def judge_single_window(metrics: Dict[str, float]) -> Tuple[str, List[str]]:
    """
    1つの期間（例：20日）に対して判定する

    戻り値:
      (level, reasons)
      level: "FULL" / "LIGHT" / "STOP"
      reasons: 日本語の理由リスト
    """

    reasons: List[str] = []

    dd = metrics.get("max_drawdown_pct", 1.0)
    pf = metrics.get("profit_factor", 0.0)
    trades = metrics.get("trades", 0)

    # -------------------------
    # FULL 判定
    # -------------------------
    full = GATE_THRESHOLDS["FULL"]
    if (
        dd <= full["max_dd_pct"]
        and pf >= full["min_pf"]
        and trades >= full["min_trades"]
    ):
        reasons.append("成績が安定しており、問題ありません。")
        return "FULL", reasons

    # -------------------------
    # LIGHT 判定
    # -------------------------
    light = GATE_THRESHOLDS["LIGHT"]
    light_reasons: List[str] = []

    if dd > light["max_dd_pct"]:
        light_reasons.append(
            f"資産の落ち込みが大きめです（最大 {dd*100:.1f}%）。"
        )
    if pf < light["min_pf"]:
        light_reasons.append(
            f"利益と損失がほぼ同じです（PF={pf:.2f}）。"
        )
    if trades < light["min_trades"]:
        light_reasons.append(
            f"取引回数が少なく、信頼性が低めです（{trades}回）。"
        )

    if not light_reasons:
        reasons.append("成績は十分ではありませんが、大きな問題はありません。")
        return "LIGHT", reasons

    # -------------------------
    # STOP 判定
    # -------------------------
    reasons.extend(light_reasons)
    reasons.append("現時点では自動売買を止めるのが安全です。")
    return "STOP", reasons


# =========================================================
# 複数期間（20 / 60 / 120日）をまとめて判定
# =========================================================
def judge_multi_window(
    metrics_by_window: Dict[int, Dict[str, float]]
) -> Dict[str, object]:
    """
    複数期間の結果から、最終ゲート判定を行う

    引数:
      metrics_by_window:
        {
          20: {...},
          60: {...},
          120: {...}
        }

    戻り値:
      {
        "gate_level": "FULL" / "LIGHT" / "STOP",
        "reasons": [日本語理由...],
        "detail": {
            20: "FULL",
            60: "LIGHT",
            120: "STOP",
        }
      }
    """

    detail: Dict[int, str] = {}
    reasons: List[str] = []

    levels = []

    for window, metrics in metrics_by_window.items():
        level, rs = judge_single_window(metrics)
        detail[window] = level
        levels.append(level)

        # 各期間の簡易理由（UI表示用）
        if level != "FULL":
            reasons.append(
                f"{window}日検証：{level}（{ ' / '.join(rs) }）"
            )

    # -------------------------
    # 最終レベル決定ルール
    # -------------------------
    # ルール：
    # - 20日が STOP → 全体 STOP
    # - 20日 OK だが、60 or 120 が悪い → LIGHT
    # - 全部 OK → FULL

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