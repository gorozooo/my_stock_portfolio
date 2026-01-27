# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/auto_fix.py

これは何？
- Judge が NO_GO を出したときに、policy（active.yml相当）を「人が触らず」修正案を自動生成し、
  バックテスト→Judge を繰り返して GO になった案を返す “自動修正係（Auto Fixer）”。

狙い（重要）
- 初心者がパラメータをいじらなくていい（＝触るのは YES/NO だけ）
- NO_GO の場合でも、システム側が「この方向で直すと良さそう」を提案し、合格した案だけを採用する
- しきい値を dev/prod で分けても、AutoFix は「指定モードのJudge」で合格を目指す

設計方針（安全）
- “戦略ロジック自体” は変えない（VWAPPullbackLongStrategy の判定ロジックは触らない）
- policy のパラメータ（利確、保有時間、早期撤退、VWAP exit grace 等）を段階的に調整する
- 候補が暴走しないように上限 max_candidates を必ず守る
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Tuple

from .judge import JudgeResult, judge_backtest_results


@dataclass
class FixCandidate:
    """
    1つの修正案（候補）
    """
    name: str
    policy: Dict[str, Any]
    judge: JudgeResult


@dataclass
class AutoFixResult:
    """
    AutoFix の結果
    """
    base_judge: JudgeResult
    candidates: List[FixCandidate]
    best: FixCandidate


# =========================
# dict 操作ヘルパー
# =========================

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


def _score_candidate(j: JudgeResult) -> float:
    """
    候補の良さを数値化（高いほど良い）
    - GO を最優先
    - avg_r を重視
    - DDは小さいほど良い
    - 連敗/日次制限ヒット率も軽く減点
    """
    decision_bonus = 1000.0 if j.decision == "GO" else 0.0

    m = j.metrics or {}
    avg_r = _safe_float(m.get("avg_r", -999), -999)
    max_dd_pct = _safe_float(m.get("max_dd_pct", 9.0), 9.0)
    max_consec = _safe_float(m.get("max_consecutive_losses", 99), 99)
    daylimit = _safe_float(m.get("daylimit_days_pct", 1.0), 1.0)

    # ざっくり：期待値↑、DD↓、荒れ↓
    return (
        decision_bonus
        + (avg_r * 120.0)
        - (max_dd_pct * 80.0)
        - (max_consec * 2.0)
        - (daylimit * 30.0)
    )


def evaluate_policy(
    policy: Dict[str, Any],
    day_results: List[Any],
    mode: str = "prod",
) -> JudgeResult:
    """
    指定された日次結果で policy を Judge する。
    """
    return judge_backtest_results(day_results, policy, mode=mode)


# =========================
# Auto Fix 本体
# =========================

def auto_fix_policy(
    base_policy: Dict[str, Any],
    day_results_provider: Callable[[Dict[str, Any]], List[Any]],
    max_candidates: int = 12,
    mode: str = "prod",
) -> AutoFixResult:
    """
    base_policy を起点に、修正案を順に試して GO を目指す。

    Parameters
    ----------
    base_policy : dict
        load_policy_yaml().policy
    day_results_provider : callable(policy)->List[DayResult]
        指定policyでバックテスト（期間）を回した日次結果を返す関数
    max_candidates : int
        生成・評価する候補の最大数（安全上の上限）
    mode : str
        "dev" or "prod"（judge.py のしきい値選択に使う）

    Returns
    -------
    AutoFixResult
    """
    mode = (mode or "prod").strip().lower()

    # ---- base評価 ----
    base_day_results = day_results_provider(base_policy)
    base_judge = evaluate_policy(base_policy, base_day_results, mode=mode)

    candidates: List[FixCandidate] = []

    # 候補重複防止（同一パラメータセットは1回だけ試す）
    seen_sigs = set()

    def _sig(p: Dict[str, Any]) -> Tuple[Any, ...]:
        """
        AutoFix が触る範囲だけで signature を作る（重複回避用）
        """
        return (
            _get_nested(p, ["exit", "take_profit_r"], None),
            _get_nested(p, ["exit", "max_hold_minutes"], None),
            _get_nested(p, ["exit", "vwap_exit_grace", "enable"], None),
            _get_nested(p, ["exit", "vwap_exit_grace", "min_r_to_allow_exit"], None),
            _get_nested(p, ["exit", "vwap_exit_grace", "grace_minutes_after_entry"], None),
            _get_nested(p, ["exec_guards", "early_stop", "enable"], None),
            _get_nested(p, ["exec_guards", "early_stop", "max_adverse_r"], None),
        )

    def _try_candidate(name: str, edits: List[Tuple[List[str], Any]]) -> Optional[FixCandidate]:
        if len(candidates) >= int(max_candidates):
            return None
        p2 = deepcopy(base_policy)
        for path, val in edits:
            _set_nested(p2, path, val)

        sig = _sig(p2)
        if sig in seen_sigs:
            return None
        seen_sigs.add(sig)

        dr = day_results_provider(p2)
        j = evaluate_policy(p2, dr, mode=mode)

        cand = FixCandidate(name=name, policy=p2, judge=j)
        candidates.append(cand)
        return cand

    # ---- 現状値（基準） ----
    tp_now = _safe_float(_get_nested(base_policy, ["exit", "take_profit_r"], 1.5), 1.5)
    mh_now = _safe_int(_get_nested(base_policy, ["exit", "max_hold_minutes"], 25), 25)

    vwap_grace_enable = bool(_get_nested(base_policy, ["exit", "vwap_exit_grace", "enable"], True))
    vwap_min_r_now = _safe_float(_get_nested(base_policy, ["exit", "vwap_exit_grace", "min_r_to_allow_exit"], 0.3), 0.3)
    vwap_grace_min_now = _safe_int(_get_nested(base_policy, ["exit", "vwap_exit_grace", "grace_minutes_after_entry"], 5), 5)

    es_enable = bool(_get_nested(base_policy, ["exec_guards", "early_stop", "enable"], True))
    es_max_adv_now = _safe_float(_get_nested(base_policy, ["exec_guards", "early_stop", "max_adverse_r"], 0.5), 0.5)

    # ---- NO_GO理由（文字列） ----
    reasons_text = " ".join(list(base_judge.reasons or []))

    # ---- 修正戦略（優先順） ----
    # 1) avg_r が低い：利確伸ばす / VWAP exit を少し我慢 / 保有時間を適正化（長すぎダメもある）
    # 2) DDが大きい：早期撤退強化 / 保有時間短縮 / VWAP exit を厳しく
    # 3) 連敗が大きい：早期撤退強化 / 保有時間短縮
    # 4) 日次制限ヒットが多い：早期撤退強化 / 保有時間短縮（荒れ耐性）

    # ---- まずは「単独変更」候補を作る（原因に合わせて） ----
    want_raise_tp = ("avg_r" in reasons_text) or ("avg_r too low" in reasons_text)
    want_reduce_dd = ("max_dd_pct" in reasons_text) or ("drawdown" in reasons_text)
    want_reduce_consec = ("max_consecutive_losses" in reasons_text)
    want_reduce_daylimit = ("daylimit_days_pct" in reasons_text)

    # avg_r改善：TPを段階的に上げる
    if want_raise_tp or base_judge.decision == "NO_GO":
        for tp in [1.6, 1.8, 2.0, 2.2, 2.5, 3.0]:
            if tp <= tp_now:
                continue
            cand = _try_candidate(
                name=f"raise_take_profit_r_to_{tp}",
                edits=[(["exit", "take_profit_r"], tp)],
            )
            if cand and cand.judge.decision == "GO":
                return AutoFixResult(base_judge=base_judge, candidates=candidates, best=cand)

    # DD削減：early_stop を強める（逆行初動で撤退）
    if want_reduce_dd or want_reduce_consec or want_reduce_daylimit:
        for adv in [0.45, 0.40, 0.35]:
            if adv >= es_max_adv_now:
                continue
            cand = _try_candidate(
                name=f"tighten_early_stop_max_adverse_r_to_{adv}",
                edits=[
                    (["exec_guards", "early_stop", "enable"], True),
                    (["exec_guards", "early_stop", "max_adverse_r"], adv),
                ],
            )
            if cand and cand.judge.decision == "GO":
                return AutoFixResult(base_judge=base_judge, candidates=candidates, best=cand)

    # ダラダラ負け抑制：最大保有時間を短縮
    if want_reduce_dd or want_reduce_consec or want_reduce_daylimit or base_judge.decision == "NO_GO":
        for mh in [20, 18, 15, 12, 10, 8]:
            if mh >= mh_now:
                continue
            cand = _try_candidate(
                name=f"reduce_max_hold_minutes_to_{mh}",
                edits=[(["exit", "max_hold_minutes"], mh)],
            )
            if cand and cand.judge.decision == "GO":
                return AutoFixResult(base_judge=base_judge, candidates=candidates, best=cand)

    # avg_r改善：VWAP exit を少し我慢（早すぎる損切り/利確を減らす意図）
    if want_raise_tp or base_judge.decision == "NO_GO":
        # 利益が乗ってない段階でのVWAP離脱を減らす（min_r を下げる＝許可を広げる）
        for min_r in [0.25, 0.20, 0.15]:
            if min_r >= vwap_min_r_now:
                continue
            cand = _try_candidate(
                name=f"relax_vwap_exit_min_r_to_{min_r}",
                edits=[
                    (["exit", "vwap_exit_grace", "enable"], True),
                    (["exit", "vwap_exit_grace", "min_r_to_allow_exit"], min_r),
                ],
            )
            if cand and cand.judge.decision == "GO":
                return AutoFixResult(base_judge=base_judge, candidates=candidates, best=cand)

        # graceを短めにして「序盤のノイズで切られる」を減らす（少し延長も試す）
        for gm in [6, 7, 8]:
            if gm <= vwap_grace_min_now:
                continue
            cand = _try_candidate(
                name=f"extend_vwap_grace_minutes_to_{gm}",
                edits=[
                    (["exit", "vwap_exit_grace", "enable"], True),
                    (["exit", "vwap_exit_grace", "grace_minutes_after_entry"], gm),
                ],
            )
            if cand and cand.judge.decision == "GO":
                return AutoFixResult(base_judge=base_judge, candidates=candidates, best=cand)

    # ---- 次に「軽い組み合わせ」候補（単独がダメなら、現場はだいたい複合で効く） ----
    # ただし爆発しないように、少数パターンだけ試す
    if base_judge.decision == "NO_GO":
        combo_plans: List[Tuple[str, List[Tuple[List[str], Any]]]] = []

        # 期待値弱い → TP少し上げる + VWAP exitを少し我慢
        combo_plans.append(
            (
                "combo_tp_up_and_vwap_relax",
                [
                    (["exit", "take_profit_r"], max(tp_now, 2.0)),
                    (["exit", "vwap_exit_grace", "enable"], True),
                    (["exit", "vwap_exit_grace", "min_r_to_allow_exit"], min(vwap_min_r_now, 0.2)),
                ],
            )
        )

        # DDきつい → early_stop強化 + 保有短縮
        combo_plans.append(
            (
                "combo_dd_cut_earlystop_and_hold_reduce",
                [
                    (["exec_guards", "early_stop", "enable"], True),
                    (["exec_guards", "early_stop", "max_adverse_r"], min(es_max_adv_now, 0.4)),
                    (["exit", "max_hold_minutes"], min(mh_now, 15)),
                ],
            )
        )

        # どっちも微妙 → TP上げ + 保有短縮（伸ばすより回転の方が効く場合がある）
        combo_plans.append(
            (
                "combo_tp_up_and_hold_reduce",
                [
                    (["exit", "take_profit_r"], max(tp_now, 2.0)),
                    (["exit", "max_hold_minutes"], min(mh_now, 15)),
                ],
            )
        )

        for name, edits in combo_plans:
            cand = _try_candidate(name=name, edits=edits)
            if cand and cand.judge.decision == "GO":
                return AutoFixResult(base_judge=base_judge, candidates=candidates, best=cand)

    # ---- GOが無いなら「一番マシ」を返す ----
    if candidates:
        best = max(candidates, key=lambda c: _score_candidate(c.judge))
    else:
        best = FixCandidate(name="base_policy", policy=base_policy, judge=base_judge)

    return AutoFixResult(base_judge=base_judge, candidates=candidates, best=best)