from datetime import datetime, timedelta

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.types import Bar
from aiapp.services.daytrade.backtest_runner import run_backtest_one_day
from aiapp.services.daytrade.auto_fix import auto_fix_policy


def make_dummy_bars_one_day(day_index: int):
    """
    ダミー1日分のbarsを作る（デモ用）。
    day_index によって勝ち/負けを混ぜ、JudgeとAutoFixの動きを見る。
    """
    bars = []
    start = datetime(2026, 1, 1, 9, 15) + timedelta(days=day_index)
    vwap = 1000.0

    bars.append(Bar(dt=start, open=1000.0, high=1002.0, low=999.8, close=1000.5, vwap=vwap, volume=1000))
    bars.append(Bar(dt=start + timedelta(minutes=1), open=1004.8, high=1005.2, low=1000.1, close=1001.0, vwap=vwap, volume=1001))
    bars.append(Bar(dt=start + timedelta(minutes=2), open=1000.8, high=1005.1, low=1000.6, close=1002.0, vwap=vwap, volume=1002))

    # 5日に1回は負け
    if day_index % 5 == 0:
        bars.append(Bar(dt=start + timedelta(minutes=15), open=1000.0, high=1000.2, low=998.5, close=998.8, vwap=vwap, volume=2000))
    else:
        # 勝ちっぽい足
        bars.append(Bar(dt=start + timedelta(minutes=15), open=1002.0, high=1006.0, low=1001.8, close=1005.5, vwap=vwap, volume=2200))

    return bars


def build_day_results_for_policy(policy: dict, days: int = 60):
    """
    指定policyで、ダミー60日分の day_results を作る。
    ※ 将来ここを「実データの1分足」生成に差し替える
    """
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

    def provider(p: dict):
        return build_day_results_for_policy(p, days=60)

    result = auto_fix_policy(base_policy, provider, max_candidates=10)

    print("---- base judge ----")
    print("decision =", result.base_judge.decision)
    print("reasons  =", result.base_judge.reasons)
    print("metrics  =", result.base_judge.metrics)

    print("---- candidates tried ----")
    for i, c in enumerate(result.candidates, 1):
        tp = c.policy.get("exit", {}).get("take_profit_r")
        mh = c.policy.get("exit", {}).get("max_hold_minutes")
        print(f"{i}. {c.name}  (tp={tp}, mh={mh}) -> {c.judge.decision}  reasons={c.judge.reasons}  avg_r={c.judge.metrics.get('avg_r')}")

    print("---- best ----")
    b = result.best
    tp = b.policy.get("exit", {}).get("take_profit_r")
    mh = b.policy.get("exit", {}).get("max_hold_minutes")
    print("best_name =", b.name)
    print("best tp =", tp, "best mh =", mh)
    print("best decision =", b.judge.decision)
    print("best reasons  =", b.judge.reasons)
    print("best metrics  =", b.judge.metrics)


main()