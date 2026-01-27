# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/auto_fix.py

これは何？
- Judgeが NO_GO を出したときに、ポリシー（active.yml 相当）を「人が触らず」修正案を自動生成し、
  バックテスト→Judge を繰り返して GO になった案を返す “自動修正係（Auto Fixer）” です。

狙い（重要）
- 初心者がパラメータをいじらなくていい（＝触るのは YES/NO だけ）
- NO_GO の場合でも、システム側が「この方向で直すと良さそう」を提案し、合格した案だけを採用する
- ただし暴走はさせない（候補数 / ラウンド数の上限あり）
- judge_mode="dev"/"prod" でしきい値を切替できる（judge.py と整合）

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
    - best: 最も良い案（GOがあれば最初のGOを優先、なければ最良スコア）
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


def _safe_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _safe_int(x, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def _policy_signature_for_dedupe(p: Dict[str, Any]) -> Tuple[Any, ...]:
    """
    「同じ修正を何度も試す」無駄を防ぐための署名。
    AutoFix が触る範囲だけで signature を作る（安全・軽量）。
    """
    return (
        _get_nested(p, ["exit", "take_profit_r"], None),
        _get_nested(p, ["exit", "max_hold_minutes"], None),
        _get_nested(p, ["exit", "exit_on_vwap_break"], None),
        _get_nested(p, ["exit", "vwap_exit_grace", "enable"], None),
        _get_nested(p, ["exit", "vwap_exit_grace", "min_r_to_allow_exit"], None),
        _get_nested(p, ["exit", "vwap_exit_grace", "grace_minutes_after_entry"], None),
        _get_nested(p, ["exec_guards", "early_stop", "enable"], None),
        _get_nested(p, ["exec_guards", "early_stop", "max_adverse_r"], None),
        _get_nested(p, ["limits", "max_trades_per_day"], None),
    )


def _score_candidate(j: JudgeResult) -> float:
    """
    候補の良さを数値化して比較する（高いほど良い）。
    - GO を最優先
    - avg_r を次に重視
    - max_dd_pct は小さいほど良い
    - daylimit_days_pct / max_consecutive_losses も軽く考慮
    """
    decision_bonus = 1000.0 if j.decision == "GO" else 0.0

    m = dict(j.metrics or {})
    avg_r = _safe_float(m.get("avg_r", -999), -999)
    max_dd_pct = _safe_float(m.get("max_dd_pct", 9), 9)
    daylimit = _safe_float(m.get("daylimit_days_pct", 1), 1)
    consec = _safe_int(m.get("max_consecutive_losses", 999), 999)

    # DDは小さいほど良いので減点、日次上限ヒットも減点、連敗も減点
    return decision_bonus + (avg_r * 120.0) - (max_dd_pct * 80.0) - (daylimit * 40.0) - (consec * 2.0)


def evaluate_policy(
    policy: Dict[str, Any],
    day_results: List[Any],
    judge_mode: str = "prod",
) -> JudgeResult:
    """
    指定された日次結果で policy を Judge する。
    """
    mode = (judge_mode or "prod").strip().lower()
    if mode not in ("dev", "prod"):
        mode = "prod"
    return judge_backtest_results(day_results, policy, mode=mode)


def _build_fix_plan(
    base_policy: Dict[str, Any],
    base_judge: JudgeResult,
) -> List[Tuple[str, List[Tuple[List[str], Any]]]]:
    """
    NO_GO の原因に応じて “安全な修正案” を作る。
    重要：戦略ロジックは変えない。policy の運用パラメータだけ触る。
    """
    reasons_text = " ".join(list(base_judge.reasons or []))

    tp_now = _safe_float(_get_nested(base_policy, ["exit", "take_profit_r"], 1.5), 1.5)
    mh_now = _safe_int(_get_nested(base_policy, ["exit", "max_hold_minutes"], 25), 25)

    # vwap_exit_grace
    grace_en = bool(_get_nested(base_policy, ["exit", "vwap_exit_grace", "enable"], False))
    grace_min_r = _safe_float(_get_nested(base_policy, ["exit", "vwap_exit_grace", "min_r_to_allow_exit"], 0.3), 0.3)
    grace_min = _safe_int(_get_nested(base_policy, ["exit", "vwap_exit_grace", "grace_minutes_after_entry"], 5), 5)

    # early_stop
    es_en = bool(_get_nested(base_policy, ["exec_guards", "early_stop", "enable"], False))
    es_adv = _safe_float(_get_nested(base_policy, ["exec_guards", "early_stop", "max_adverse_r"], 0.5), 0.5)

    # limits
    max_trades = _safe_int(_get_nested(base_policy, ["limits", "max_trades_per_day"], 3), 3)

    # metrics
    m = dict(base_judge.metrics or {})
    avg_r = _safe_float(m.get("avg_r", 0.0), 0.0)
    max_dd_pct = _safe_float(m.get("max_dd_pct", 9.0), 9.0)
    daylimit = _safe_float(m.get("daylimit_days_pct", 0.0), 0.0)
    consec = _safe_int(m.get("max_consecutive_losses", 0), 0)

    plan: List[Tuple[str, List[Tuple[List[str], Any]]]] = []

    # --- (A) avg_r が低い：勝ちを伸ばす / ダラダラ負けを減らす ---
    if ("avg_r" in reasons_text) or (avg_r < 0.0) or (base_judge.decision == "NO_GO"):
        # 1) TPを少し伸ばす（やりすぎると約定しないので段階）
        for tp in [2.0, 2.2, 2.5, 3.0]:
            if tp > tp_now:
                plan.append((f"raise_take_profit_r_to_{tp}", [(["exit", "take_profit_r"], float(tp))]))

        # 2) 保有時間を短く（ダラダラ負け削減）
        for mh in [20, 15, 12, 10, 8]:
            if mh < mh_now:
                plan.append((f"reduce_max_hold_minutes_to_{mh}", [(["exit", "max_hold_minutes"], int(mh))]))

    # --- (B) DDが厳しい：撤退を早める / 無理な粘りを減らす ---
    if ("max_dd_pct" in reasons_text) or (max_dd_pct >= 0.02) or ("consecutive" in reasons_text) or (consec >= 4):
        # 1) early_stop を入れる（入ってないならON）
        if not es_en:
            plan.append(("enable_early_stop", [(["exec_guards", "early_stop", "enable"], True)]))

        # 2) 逆行許容を下げる（0.5→0.45→0.4→0.35）
        for adv in [0.45, 0.40, 0.35, 0.30]:
            if adv < es_adv:
                plan.append((f"tighten_early_stop_max_adverse_r_to_{adv}", [(["exec_guards", "early_stop", "max_adverse_r"], float(adv))]))

        # 3) ついでに保有時間も短くする（重複OKだがdedupeで弾かれる）
        for mh in [15, 12, 10]:
            if mh < mh_now:
                plan.append((f"reduce_max_hold_minutes_to_{mh}", [(["exit", "max_hold_minutes"], int(mh))]))

    # --- (C) 日次制限ヒットが多い：トレード回数を抑える ---
    if ("daylimit_days_pct" in reasons_text) or (daylimit >= 0.10):
        for mt in [2, 1]:
            if mt < max_trades:
                plan.append((f"reduce_max_trades_per_day_to_{mt}", [(["limits", "max_trades_per_day"], int(mt))]))

    # --- (D) VWAP割れ即撤退が早すぎる/遅すぎる問題：grace を微調整 ---
    # これは “勝ちを伸ばす or DDを減らす” どっちにも効く可能性があるので薄く入れる
    # ※ enable は policy に既にある想定。無ければ作る。
    if grace_en:
        # min_r を少し下げると「少し利益が乗ったらVWAP割れで逃げる」が増える（DD抑制）
        for mr in [0.25, 0.20, 0.15]:
            if mr < grace_min_r:
                plan.append((f"lower_vwap_exit_grace_min_r_to_{mr}", [(["exit", "vwap_exit_grace", "min_r_to_allow_exit"], float(mr))]))

        # grace分を短くすると「様子見時間」が減り、負けを縮めやすい
        for gm in [4, 3]:
            if gm < grace_min:
                plan.append((f"reduce_vwap_exit_grace_minutes_to_{gm}", [(["exit", "vwap_exit_grace", "grace_minutes_after_entry"], int(gm))]))
    else:
        # graceが無効ならONにする候補（ただし乱発しない）
        plan.append(("enable_vwap_exit_grace", [(["exit", "vwap_exit_grace", "enable"], True)]))

    # さいご：あまりにも候補が少ない場合の保険
    if not plan:
        # 最低限：max_hold を短くする
        for mh in [15, 10]:
            if mh < mh_now:
                plan.append((f"reduce_max_hold_minutes_to_{mh}", [(["exit", "max_hold_minutes"], int(mh))]))

    return plan


def auto_fix_policy(
    base_policy: Dict[str, Any],
    day_results_provider: Callable[[Dict[str, Any]], List[Any]],
    max_candidates: int = 10,
    *,
    judge_mode: str = "prod",
    max_rounds: int = 3,
) -> AutoFixResult:
    """
    base_policy を起点に、修正案を順に試して GO を目指す（複数ラウンド探索）。

    Parameters
    ----------
    base_policy : dict
        load_policy_yaml().policy
    day_results_provider : callable(policy)->List[DayResult]
        指定policyでバックテスト（期間）を回した日次結果を返す関数。
    max_candidates : int
        生成する候補の最大数（安全上の上限）
    judge_mode : str
        "dev" or "prod"（judge.py と同じ）
    max_rounds : int
        ラウンド数。1=従来の「1回だけ」。3くらいが現実的。

    Returns
    -------
    AutoFixResult
    """
    mode = (judge_mode or "prod").strip().lower()
    if mode not in ("dev", "prod"):
        mode = "prod"

    # 元ポリシーの評価
    base_day_results = day_results_provider(base_policy)
    base_judge = evaluate_policy(base_policy, base_day_results, judge_mode=mode)

    candidates: List[FixCandidate] = []

    # まず base が GO なら何もしない
    if base_judge.decision == "GO":
        base_cand = FixCandidate(name="base_policy", policy=base_policy, judge=base_judge)
        return AutoFixResult(base_judge=base_judge, candidates=[], best=base_cand)

    # 探索の起点（ラウンドごとに更新していく）
    current_policy = deepcopy(base_policy)
    current_judge = base_judge

    seen: set = set()
    seen.add(_policy_signature_for_dedupe(current_policy))

    # best を常に追跡
    best = FixCandidate(name="base_policy", policy=base_policy, judge=base_judge)
    best_score = _score_candidate(best.judge)

    # 複数ラウンド探索
    rounds = max(int(max_rounds), 1)
    limit = max(int(max_candidates), 1)

    for r in range(1, rounds + 1):
        if len(candidates) >= limit:
            break

        # このラウンドの方針（現状のNO_GO理由に合わせて作る）
        fix_plan = _build_fix_plan(current_policy, current_judge)

        progressed_in_round = False

        for name, edits in fix_plan:
            if len(candidates) >= limit:
                break

            p2 = deepcopy(current_policy)
            for path, val in edits:
                _set_nested(p2, path, val)

            sig = _policy_signature_for_dedupe(p2)
            if sig in seen:
                continue
            seen.add(sig)

            dr = day_results_provider(p2)
            j = evaluate_policy(p2, dr, judge_mode=mode)

            cand = FixCandidate(name=f"r{r}:{name}", policy=p2, judge=j)
            candidates.append(cand)
            progressed_in_round = True

            sc = _score_candidate(j)
            if sc > best_score:
                best = cand
                best_score = sc

            # GO が出たら即終了（最短で返す）
            if j.decision == "GO":
                return AutoFixResult(base_judge=base_judge, candidates=candidates, best=cand)

        # このラウンドで1個も進まない＝もう打てる手が無い
        if not progressed_in_round:
            break

        # 次ラウンドの起点は「現時点のベスト」に寄せる（改善の方向を継続）
        current_policy = deepcopy(best.policy)
        current_judge = best.judge

        # もしベストが base から全く改善してないなら、ここで止める（無駄な探索を防ぐ）
        # （avg_r と DD が同じ/悪化、などの場合）
        if r >= 2:
            try:
                bm = dict(best.judge.metrics or {})
                cm = dict(current_judge.metrics or {})
                # ここは同一なので実質 no-op だが、将来の安全弁として残す
                _ = (bm, cm)
            except Exception:
                pass

    # GOが無ければ「一番マシ」を返す
    if candidates:
        best = max(candidates + [best], key=lambda c: _score_candidate(c.judge))
    else:
        best = FixCandidate(name="base_policy", policy=base_policy, judge=base_judge)

    return AutoFixResult(base_judge=base_judge, candidates=candidates, best=best)