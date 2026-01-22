from datetime import datetime, timedelta

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.types import Bar
from aiapp.services.daytrade.backtest_runner import run_backtest_one_day
from aiapp.services.daytrade.auto_fix import auto_fix_policy


def make_dummy_bars_one_day(day_index: int):
    """
    デモ用：1日分の1分足バーを作る（25本）。

    目的：
    - take_profit_r を変えると“利確の有無/タイミング”が変わる
    - max_hold_minutes を変えると“時間切れ”が変わる

    設計：
    - まずエントリーしやすい形を数本作る
    - その後、勝ち日/負け日でトレンドを分ける
    """
    bars = []
    start = datetime(2026, 1, 1, 9, 15) + timedelta(days=day_index)
    vwap = 1000.0

    # 0: 初期
    bars.append(Bar(dt=start, open=1000.0, high=1002.0, low=999.8, close=1000.5, vwap=vwap, volume=1000))
    # 1: 上
    bars.append(Bar(dt=start + timedelta(minutes=1), open=1000.6, high=1002.0, low=1000.1, close=1001.0, vwap=vwap, volume=1100))
    # 2: 反発（エントリーしやすい）
    bars.append(Bar(dt=start + timedelta(minutes=2), open=1000.9, high=1002.2, low=1000.6, close=1002.0, vwap=vwap, volume=1200))

    # 以降 3..24 を生成
    # 5日に1回は負け日（ストップ方向へ下げる）
    lose_day = (day_index % 5 == 0)

    px = 1002.0
    for k in range(3, 25):
        dt = start + timedelta(minutes=k)

        if lose_day:
            # 徐々に下げてストップ（vwap*(1-0.001)=999）方向へ
            # 早めに刺さるように強めに下げる
            px -= 0.6
        else:
            # 徐々に上げてTPに届く可能性を作る
            px += 0.6

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
        print(
            f"{i}. {c.name} (tp={tp}, mh={mh}) -> {c.judge.decision} "
            f"reasons={c.judge.reasons} avg_r={c.judge.metrics.get('avg_r')}"
        )

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