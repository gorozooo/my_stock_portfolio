from datetime import date

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.judge import judge_backtest_results
from aiapp.services.daytrade.judge_snapshot import save_judge_snapshot


def main():
    loaded = load_policy_yaml()
    policy = loaded.policy

    # デモなので、空のday_results（本番は期間バックテスト結果を入れる）
    # ここでは「GOのときに保存できる」動作確認をするなら、
    # 先に作った daytrade_auto_fix_demo の day_results を使うのが理想。
    # 今回は最低限の保存動作確認用に、judge_resultだけ作る。
    day_results = []  # 本番では入る
    judge = judge_backtest_results(day_results, policy)

    p = save_judge_snapshot(
        date.today(),
        policy,
        judge,
        extra={"note": "demo snapshot"},
    )

    print("saved:", p)


main()