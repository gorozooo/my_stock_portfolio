from datetime import date, datetime, timedelta

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.types import Bar
from aiapp.services.daytrade.backtest_runner import run_backtest_one_day
from aiapp.services.daytrade.auto_fix import auto_fix_policy
from aiapp.services.daytrade.judge import judge_backtest_results
from aiapp.services.daytrade.judge_snapshot import save_judge_snapshot


def make_entry_pattern(start: datetime, vwap: float):
    bars = []
    bars.append(Bar(dt=start, open=1000.0, high=1006.0, low=999.9, close=1005.0, vwap=vwap, volume=1200))
    bars.append(Bar(dt=start + timedelta(minutes=1), open=1004.5, high=1004.8, low=999.9, close=1000.4, vwap=vwap, volume=1300))
    bars.append(Bar(dt=start + timedelta(minutes=2), open=1000.3, high=1002.5, low=1000.1, close=1001.8, vwap=vwap, volume=1600))
    return bars


def make_dummy_bars_one_day(day_index: int):
    bars = []
    start = datetime(2026, 1, 1, 9, 15) + timedelta(days=day_index)
    vwap = 1000.0

    bars.extend(make_entry_pattern(start, vwap))

    lose_day = (day_index % 5 == 0)

    px = bars[-1].close
    for k in range(3, 30):
        dt = start + timedelta(minutes=k)

        if lose_day:
            px -= 0.5
        else:
            px += 0.7

        o = px - 0.2
        c = px
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3

        bars.append(Bar(dt=dt, open=o, high=h, low=l, close=c, vwap=vwap, volume=1500 + k))

    return bars


def build_day_results_for_policy(policy: dict, days: int = 60):
    day_results = []
    for d in range(days):
        bars = make_dummy_bars_one_day(d)
        res = run_backtest_one_day(bars, policy)
        day_results.append(res)
    return day_results


def main():
    loaded = load_policy_yaml()
    base_policy = loaded.policy

    print("policy_id =", base_policy.get("meta", {}).get("policy_id"))
    print("base exit.take_profit_r =", base_policy.get("exit", {}).get("take_profit_r"))
    print("base exit.max_hold_minutes =", base_policy.get("exit", {}).get("max_hold_minutes"))
    print("slippage_buffer_pct =", base_policy.get("risk", {}).get("slippage_buffer_pct"))

    # 1) base の day_results を作る（本番では実データに置換）
    base_day_results = build_day_results_for_policy(base_policy, days=60)

    # 2) base Judge
    base_judge = judge_backtest_results(base_day_results, base_policy)

    print("---- base judge ----")
    print("decision =", base_judge.decision)
    print("reasons  =", base_judge.reasons)
    print("metrics  =", base_judge.metrics)

    # 3) NO_GO の時だけ AutoFix（GOなら何もしない）
    def provider(p: dict):
        return build_day_results_for_policy(p, days=60)

    result = auto_fix_policy(base_policy, provider, max_candidates=10)

    # best 判定（GOのときは base_policy をbestとして返る）
    best = result.best

    # 4) best を snapshot 保存（policyも全部残す）
    snap_path = save_judge_snapshot(
        date.today(),
        best.policy,
        best.judge,
        extra={
            "mode": "demo_dummy",
            "days": 60,
            "base_policy_id": (base_policy.get("meta") or {}).get("policy_id"),
            "best_name": best.name,
            "base_decision": base_judge.decision,
        },
    )

    print("---- best ----")
    print("best_name =", best.name)
    print("best decision =", best.judge.decision)
    print("best reasons  =", best.judge.reasons)
    print("best metrics  =", best.judge.metrics)
    print("saved snapshot =", snap_path)


main()