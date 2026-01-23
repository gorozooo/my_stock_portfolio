# -*- coding: utf-8 -*-
"""
ファイル: scripts/daytrade_early_stop_min_test.py

目的（最小検証）
- ExecutionGuard1m.should_early_exit() が意図どおり発火するかを確認する。
- 仕様（合意）:
  - early_stop: enable=true, max_adverse_r=0.5（active.yml）
  - planned_risk_yen は Signal 側から渡される「1トレード想定損失（円）」

この検証で分かること
- 「現実的な planned_risk_yen（例: 3000円）」で発火するのか？
- もし発火しないなら、式が単位不整合の可能性が高い（重要な設計バグ候補）。
"""

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.execution_guard import ExecutionGuard1m


def check_case(entry_price: float, current_price: float, planned_risk_yen: float, side: str, label: str):
    policy = load_policy_yaml().policy
    guard = ExecutionGuard1m(policy)

    max_adverse_r = (
        policy.get("exec_guards", {})
        .get("early_stop", {})
        .get("max_adverse_r", None)
    )

    qty = 100  # テスト用の仮数量（現実的な値でOK）

    fired = guard.should_early_exit(
        entry_price=entry_price,
        current_price=current_price,
        planned_risk_yen=planned_risk_yen,
        side=side,
        qty=qty,
    )

    if side == "long":
        adverse = entry_price - current_price
    else:
        adverse = current_price - entry_price

    # 実装どおりの r（単位不整合の可能性あり）
    r = None
    if planned_risk_yen and planned_risk_yen > 0:
        r = adverse / planned_risk_yen

    print(f"--- {label} ---")
    print(f"side={side} entry={entry_price} current={current_price} adverse(price)={adverse}")
    print(f"planned_risk_yen={planned_risk_yen}  computed_r={r}  threshold(max_adverse_r)={max_adverse_r}")
    print(f"early_stop_fired={fired}")
    print("")


def main():
    print("=== early_stop min test ===")

    # ケース設定（分かりやすい値）
    entry = 1000.0
    side = "long"

    # 1) 現実的な planned_risk_yen（例：資金100万、0.3% = 3000円）
    #    ここで「5円」「10円」「20円」逆行しても発火するかを見る
    check_case(entry, 995.0, 3000.0, side, "realistic risk: planned_risk_yen=3000, adverse=5")
    check_case(entry, 990.0, 3000.0, side, "realistic risk: planned_risk_yen=3000, adverse=10")
    check_case(entry, 980.0, 3000.0, side, "realistic risk: planned_risk_yen=3000, adverse=20")

    # 2) 参考：planned_risk_yen を極端に小さくすると発火するか（式の動作確認）
    check_case(entry, 995.0, 10.0, side, "tiny risk: planned_risk_yen=10, adverse=5")
    check_case(entry, 999.0, 2.0, side, "tiny risk: planned_risk_yen=2, adverse=1")

    print("=== done ===")


if __name__ == "__main__":
    main()