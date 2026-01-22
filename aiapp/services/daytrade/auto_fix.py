# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/auto_fix.py

これは何？
- Judgeが NO_GO を出したときに、ポリシー（active.yml 相当）を「人が触らず」修正案を自動生成し、
  バックテスト→Judge を繰り返して GO になった案を返す “自動修正係（Auto Fixer）” です。

狙い（重要）
- 初心者がパラメータをいじらなくていい（＝触るのは YES/NO だけ）
- NO_GO の場合でも、システム側が「この方向で直すと良さそう」を提案し、合格した案だけを採用する
- 機関投資家っぽい運用（基準を満たすものだけ稼働）

置き場所
- aiapp/services/daytrade/auto_fix.py
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Tuple

from .judge import JudgeResult, judge_backtest_results


@dataclass
class FixCandidate:
    """
    1つの修正案（候補）を表す。
    - name: 何をどう変えた案か（ログ用）
    - policy: 修正後の policy dict
    - judge: その案を評価した JudgeResult
    """
    name: str
    policy: Dict[str, Any]
    judge: JudgeResult


@dataclass
class AutoFixResult:
    """
    AutoFix の結果。
    - base_judge: 元ポリシーのJudge結果
    - candidates: 試した案（順番通り）
    - best: 最も良い案（GOがあれば最初のGOを優先、なければ最良のavg_r）
    """
    base_judge: JudgeResult
    candidates: List[FixCandidate]
    best: FixCandidate


def _get_nested(d: Dict[str, Any], path: List[str], default: Any = None) -> Any:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        if k not in cur:
            return default
        cur = cur[k]
    return cur


def _set_nested(d: Dict[str, Any], path: List[str], value: Any) -> None:
    cur: Any = d
    for k in path[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[path[-1]] = value


def _score_candidate(j: JudgeResult) -> float:
    """
    候補の良さを数値化して比較する（高いほど良い）。
    - GO を最優先
    - avg_r を次に重視
    - max_dd_pct は小さいほど良い
    """
    decision_bonus = 1000.0 if j.decision == "GO" else 0.0
    avg_r = float(j.metrics.get("avg_r", -999))
    max_dd_pct = float(j.metrics.get("max_dd_pct", 9))
    # DDは小さいほど良いので減点
    return decision_bonus + (avg_r * 100.0) - (max_dd_pct * 50.0)


def evaluate_policy(
    policy: Dict[str, Any],
    day_results: List[Any],
) -> JudgeResult:
    """
    指定された日次結果で policy を Judge する。
    """
    return judge_backtest_results(day_results, policy)


def auto_fix_policy(
    base_policy: Dict[str, Any],
    day_results_provider: Callable[[Dict[str, Any]], List[Any]],
    max_candidates: int = 10,
) -> AutoFixResult:
    """
    base_policy を起点に、修正案を順に試して GO を目指す。

    Parameters
    ----------
    base_policy : dict
        load_policy_yaml().policy
    day_results_provider : callable(policy)->List[DayResult]
        指定policyでバックテスト（期間）を回した日次結果を返す関数。
        ※ 実データ化したときも差し替えやすい設計
    max_candidates : int
        生成する候補の最大数（安全上の上限）

    Returns
    -------
    AutoFixResult
    """
    # 元ポリシーの評価
    base_day_results = day_results_provider(base_policy)
    base_judge = evaluate_policy(base_policy, base_day_results)

    candidates: List[FixCandidate] = []

    # 修正案テンプレ（初心者が触らない前提の“システム側の手”）
    # 優先順位：
    # 1) 平均Rが低い → 利確を伸ばす（take_profit_rを上げる）
    # 2) それでもダメ → 時間切れを短く（max_hold_minutesを下げ、ダラダラ負けを減らす）
    # 3) それでもダメ → エントリーを少し厳選（near_vwap_pctを絞る）
    fix_plan: List[Tuple[str, List[Tuple[List[str], Any]]]] = []

    # 理由に avg_r too low があるなら、まずTPを上げる
    reasons_text = " ".join(base_judge.reasons)
    if "avg_r too low" in reasons_text or base_judge.decision == "NO_GO":
        # 現在値を取り、段階的に上げる
        tp_now = float(_get_nested(base_policy, ["exit", "take_profit_r"], 1.5))
        tp_steps = [2.0, 2.5, 3.0]
        for tp in tp_steps:
            if tp <= tp_now:
                continue
            fix_plan.append(
                (f"raise_take_profit_r_to_{tp}",
                 [ (["exit", "take_profit_r"], tp) ])
            )

        # 時間切れ短縮（15→10→8）
        mh_now = int(_get_nested(base_policy, ["exit", "max_hold_minutes"], 15))
        mh_steps = [10, 8]
        for mh in mh_steps:
            if mh >= mh_now:
                continue
            fix_plan.append(
                (f"reduce_max_hold_minutes_to_{mh}",
                 [ (["exit", "max_hold_minutes"], mh) ])
            )

        # エントリー厳選（near_vwap_pctを絞る：0.20→0.15→0.12）
        # ※ entry.require が dict ではなく list かもしれないが、現状active.ymlは list 形式なので、
        #    ここでは “安全にやる” として、list構造は触らず、将来entryをdict化したときに有効化する。
        #    まずはTPと時間切れで改善させる。
        # （将来：entry.require を dict 化したらここをONにする）
        pass

    # 候補を順に試す（上限あり）
    for name, edits in fix_plan:
        if len(candidates) >= max_candidates:
            break

        p2 = deepcopy(base_policy)
        for path, val in edits:
            _set_nested(p2, path, val)

        dr = day_results_provider(p2)
        j = evaluate_policy(p2, dr)
        candidates.append(FixCandidate(name=name, policy=p2, judge=j))

        # GOが出たら最短で返す（時間も無駄にしない）
        if j.decision == "GO":
            best = candidates[-1]
            return AutoFixResult(base_judge=base_judge, candidates=candidates, best=best)

    # GOが無ければ「一番マシ」を返す
    if candidates:
        best = max(candidates, key=lambda c: _score_candidate(c.judge))
    else:
        # 候補を作れなかった場合は元を返す
        best = FixCandidate(name="base_policy", policy=base_policy, judge=base_judge)

    return AutoFixResult(base_judge=base_judge, candidates=candidates, best=best)