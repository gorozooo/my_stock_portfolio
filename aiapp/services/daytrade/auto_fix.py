# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/auto_fix.py

強化版 Auto Fixer
- NO_GO の原因に応じて複数方向の候補を作る
- 1段の候補生成だけでなく、スコア上位を起点に複数段探索する（安全上限あり）
- 目標: mode="prod" を GO にする（開発中は mode="dev" で改善サイクルを回すのもOK）
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Tuple

from .judge import JudgeResult, judge_backtest_results


@dataclass
class FixCandidate:
    name: str
    policy: Dict[str, Any]
    judge: JudgeResult
    diffs: List[Dict[str, Any]]


@dataclass
class AutoFixResult:
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


def _diff_policy_watch(base: Dict[str, Any], cand: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    どこを変えたか（安全な範囲のみ）
    """
    watch = [
        (["exit", "take_profit_r"], "利確ライン（R）"),
        (["exit", "max_hold_minutes"], "最大保有時間（分）"),
        (["exec_guards", "early_stop", "max_adverse_r"], "早期撤退（逆行R）"),
        (["limits", "max_trades_per_day"], "1日最大トレード数"),
        (["vwap_exit_grace", "min_r_to_allow_exit"], "VWAP離脱猶予（許容R）"),
    ]

    out: List[Dict[str, Any]] = []
    for path, label in watch:
        b = _get_nested(base, path, None)
        c = _get_nested(cand, path, None)
        if b != c:
            out.append({"label": label, "path": ".".join(path), "before": b, "after": c})
    return out


def _score(j: JudgeResult) -> float:
    """
    高いほど良い
    - GO を最優先
    - avg_r を重視
    - DD を減点
    - trades が少なすぎる案は軽く減点（安定性）
    """
    decision_bonus = 100000.0 if j.decision == "GO" else 0.0
    avg_r = float(j.metrics.get("avg_r", -999))
    max_dd = float(j.metrics.get("max_dd_pct", 9))
    trades = float(j.metrics.get("total_trades", 0))

    trade_penalty = 0.0
    if trades < 50:
        trade_penalty = (50 - trades) * 2.0

    return decision_bonus + (avg_r * 1000.0) - (max_dd * 300.0) - trade_penalty


def evaluate_policy(policy: Dict[str, Any], day_results: List[Any], mode: str) -> JudgeResult:
    return judge_backtest_results(day_results, policy, mode=mode)


def _generate_single_step_candidates(base_policy: Dict[str, Any], base_judge: JudgeResult) -> List[Tuple[str, List[Tuple[List[str], Any]]]]:
    """
    NO_GO理由に応じて、1手だけいじる候補を作る
    （“戦略ロジック”は変えず、ポリシーの範囲で）
    """
    reasons_text = " ".join(list(base_judge.reasons or []))

    tp_now = float(_get_nested(base_policy, ["exit", "take_profit_r"], 1.5))
    mh_now = int(_get_nested(base_policy, ["exit", "max_hold_minutes"], 25))
    es_now = float(_get_nested(base_policy, ["exec_guards", "early_stop", "max_adverse_r"], 0.5))
    mtd_now = int(_get_nested(base_policy, ["limits", "max_trades_per_day"], 3))
    vwap_minr_now = float(_get_nested(base_policy, ["exit", "vwap_exit_grace", "min_r_to_allow_exit"], 0.3))

    plans: List[Tuple[str, List[Tuple[List[str], Any]]]] = []

    # --- avg_r が低い（期待値が弱い） ---
    if ("avg_r too low" in reasons_text) or (base_judge.decision == "NO_GO"):
        # 利確を伸ばす（勝ちを伸ばす）
        for tp in [2.0, 2.5, 3.0]:
            if tp > tp_now:
                plans.append((f"raise_take_profit_r_to_{tp}", [(["exit", "take_profit_r"], tp)]))

        # 最大保有を短くしてダラダラを減らす
        for mh in [20, 15, 12, 10, 8]:
            if mh < mh_now:
                plans.append((f"reduce_max_hold_minutes_to_{mh}", [(["exit", "max_hold_minutes"], mh)]))

        # 早期撤退（逆行を切る）：0.5→0.4→0.35→0.3
        for es in [0.4, 0.35, 0.3]:
            if es < es_now:
                plans.append((f"tighten_early_stop_to_{es}", [(["exec_guards", "early_stop", "max_adverse_r"], es)]))

        # 1日トレード数を絞って負けの連鎖を止める
        for mtd in [2, 1]:
            if mtd < mtd_now:
                plans.append((f"reduce_max_trades_per_day_to_{mtd}", [(["limits", "max_trades_per_day"], mtd)]))

        # VWAP離脱を早める方向（勝ち逃げ/撤退を早くする）
        for mr in [0.2, 0.15]:
            if mr < vwap_minr_now:
                plans.append((f"allow_vwap_exit_from_{mr}R", [(["exit", "vwap_exit_grace", "min_r_to_allow_exit"], mr)]))

    # --- DD が大きい系（今後 reasons で拾えるようにしておく） ---
    if "max_dd_pct exceeded" in reasons_text:
        for es in [0.4, 0.35, 0.3]:
            if es < es_now:
                plans.append((f"dd_guard_tighten_early_stop_to_{es}", [(["exec_guards", "early_stop", "max_adverse_r"], es)]))
        for mtd in [2, 1]:
            if mtd < mtd_now:
                plans.append((f"dd_guard_reduce_max_trades_per_day_to_{mtd}", [(["limits", "max_trades_per_day"], mtd)]))

    return plans


def auto_fix_policy(
    base_policy: Dict[str, Any],
    day_results_provider: Callable[[Dict[str, Any]], List[Any]],
    max_candidates: int = 25,
    max_rounds: int = 3,
    beam_width: int = 5,
    mode: str = "prod",
) -> AutoFixResult:
    """
    強化版：複数段探索で GO を狙う
    - max_rounds: 何段探索するか
    - beam_width: 各段で上位何件を次段の起点にするか
    """
    mode = (mode or "prod").strip().lower()

    base_day_results = day_results_provider(base_policy)
    base_judge = evaluate_policy(base_policy, base_day_results, mode=mode)

    base_cand = FixCandidate(
        name="base_policy",
        policy=base_policy,
        judge=base_judge,
        diffs=[],
    )

    all_candidates: List[FixCandidate] = []
    frontier: List[FixCandidate] = [base_cand]

    best = base_cand

    for _round in range(max_rounds):
        next_gen: List[FixCandidate] = []

        for seed in frontier:
            plans = _generate_single_step_candidates(seed.policy, seed.judge)
            for name, edits in plans:
                if len(all_candidates) >= max_candidates:
                    break

                p2 = deepcopy(seed.policy)
                for path, val in edits:
                    _set_nested(p2, path, val)

                dr = day_results_provider(p2)
                j = evaluate_policy(p2, dr, mode=mode)
                diffs = _diff_policy_watch(base_policy, p2)

                cand = FixCandidate(name=f"r{_round+1}:{name}", policy=p2, judge=j, diffs=diffs)
                all_candidates.append(cand)
                next_gen.append(cand)

                # ベスト更新
                if _score(cand.judge) > _score(best.judge):
                    best = cand

                # GOが出たら即終了（時間を無駄にしない）
                if cand.judge.decision == "GO":
                    return AutoFixResult(base_judge=base_judge, candidates=all_candidates, best=cand)

            if len(all_candidates) >= max_candidates:
                break

        # 次段の起点（beam search）
        if not next_gen:
            break

        next_gen.sort(key=lambda c: _score(c.judge), reverse=True)
        frontier = next_gen[: max(int(beam_width), 1)]

    return AutoFixResult(base_judge=base_judge, candidates=all_candidates, best=best)