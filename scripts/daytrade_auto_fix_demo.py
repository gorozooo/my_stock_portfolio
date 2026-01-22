from datetime import datetime, timedelta

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.types import Bar
from aiapp.services.daytrade.backtest_runner import run_backtest_one_day
from aiapp.services.daytrade.auto_fix import auto_fix_policy


def make_entry_pattern(start: datetime, vwap: float):
    """
    戦略が入りやすい “VWAP押し目→反発” の型を作る。
    - prev.low をVWAP付近に置く
    - close>vwap の陽線反発にする
    """
    bars = []
    # 0: 上昇して高値を作る（pullback_pct用の "recent_high" を作るイメージ）
    bars.append(Bar(dt=start, open=1000.0, high=1006.0, low=999.9, close=1005.0, vwap=vwap, volume=1200))
    # 1: 押し目（lowがVWAP付近＝near_vwap）
    bars.append(Bar(dt=start+timedelta(minutes=1), open=1004.5, high=1004.8, low=999.9, close=1000.4, vwap=vwap, volume=1300))
    # 2: 反発（陽線、close>vwap）
    bars.append(Bar(dt=start+timedelta(minutes=2), open=1000.3, high=1002.5, low=1000.1, close=1001.8, vwap=vwap, volume=1600))
    return bars


def make_dummy_bars_one_day(day_index: int):
    """
    デモ用：1日分の1分足バーを作る（30本）。
    - まず必ずエントリーが出る“型”を作る
    - その後、勝ち/負けを分けてTPや時間切れが効くように伸ばす
    """
    bars = []
    start = datetime(2026, 1, 1, 9, 15) + timedelta(days=day_index)
    vwap = 1000.0

    bars.extend(make_entry_pattern(start, vwap))

    # 5日に1回は負け日
    lose_day = (day_index % 5 == 0)

    # 3分目以降を生成して、TP/SL/時間切れが起きるようにする
    px = bars[-1].close  # 1001.8
    for k in range(3, 30):
        dt = start + timedelta(minutes=k)

        if lose_day:
            # だんだん下げてストップ方向へ（vwap*(1-0.001)=999）
            px -= 0.5
        else:
            # だんだん上げて利確方向へ
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