# -*- coding: utf-8 -*-
"""
ファイル: scripts/daytrade_early_stop_min_test.py

目的（現実ケース検証）
- LiveRunner と同じ数量計算（qty）で early_stop が発火するか確認する。
- 仕様（active.yml）:
  - exec_guards.early_stop.enable=true
  - exec_guards.early_stop.max_adverse_r=0.5
- planned_risk_yen は「1トレードの想定損失（円）」= 例: 3000円（資金100万×0.3%）

検証の考え方
- stop幅（entry-stop）が小さいほど qty が増え、同じ価格逆行でも adverse_yen が増える。
- その結果、0.5R 到達が早くなる（＝早期撤退が“効きやすい”）。
"""

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.execution_guard import ExecutionGuard1m


def calc_qty_like_live_runner(planned_risk_yen: float, entry_price: float, stop_price: float) -> int:
    per_share_risk = abs(entry_price - stop_price)
    if per_share_risk <= 0:
        return 0
    qty = int(planned_risk_yen / per_share_risk)
    return max(qty, 0)


def run_one_case(entry: float, stop: float, planned_risk_yen: float, adverse_price: float, side: str):
    policy = load_policy_yaml().policy
    guard = ExecutionGuard1m(policy)

    max_adverse_r = (
        policy.get("exec_guards", {})
        .get("early_stop", {})
        .get("max_adverse_r", None)
    )

    qty = calc_qty_like_live_runner(planned_risk_yen, entry, stop)

    # 逆行方向に current を作る
    if side == "long":
        current = entry - adverse_price
        adverse = entry - current  # = adverse_price
    else:
        current = entry + adverse_price
        adverse = current - entry

    adverse_yen = adverse * qty if qty > 0 else 0.0
    computed_r = (adverse_yen / planned_risk_yen) if planned_risk_yen > 0 else None

    fired = guard.should_early_exit(
        entry_price=entry,
        current_price=current,
        planned_risk_yen=planned_risk_yen,
        side=side,
        qty=qty,
    )

    print(f"entry={entry} stop={stop} (per_share_risk={abs(entry-stop)}) planned_risk_yen={planned_risk_yen}")
    print(f"qty(live_runner)={qty}")
    print(f"adverse_price={adverse_price}  adverse_yen={adverse_yen}  computed_r={computed_r}  threshold={max_adverse_r}")
    print(f"early_stop_fired={fired}")
    print("")


def main():
    print("=== early_stop realistic test (qty like LiveRunner) ===")

    policy = load_policy_yaml().policy
    print("policy_id =", policy.get("meta", {}).get("policy_id"))

    # 現実想定（あなたの運用）：資金100万、1トレード損失上限=0.3% → 3000円
    planned_risk_yen = 3000.0
    entry = 1000.0
    side = "long"

    print("---- settings ----")
    print("planned_risk_yen =", planned_risk_yen)
    print("entry =", entry, "side =", side)
    print("")

    # stop幅の候補（例）：1円/2円/3円/5円/10円
    # ※ stopが浅いほど qty が増え、early_stop が早く出やすい
    stop_deltas = [1.0, 2.0, 3.0, 5.0, 10.0]

    # 逆行幅の候補（例）：0.5円/1円/2円/3円/5円/10円
    adverse_deltas = [0.5, 1.0, 2.0, 3.0, 5.0, 10.0]

    for sd in stop_deltas:
        stop = entry - sd
        print(f"==== stop_delta={sd} (stop={stop}) ====")
        for ad in adverse_deltas:
            run_one_case(entry, stop, planned_risk_yen, adverse_price=ad, side=side)

    print("=== done ===")


if __name__ == "__main__":
    main()