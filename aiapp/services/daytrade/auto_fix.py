# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/auto_fix.py

これは何？
- Judgeが NO_GO を出したときに、ポリシー（active.yml 相当）を「人が触らず」修正案を自動生成し、
  バックテスト→Judge を繰り返して GO になった案を返す “自動修正係（Auto Fixer）” です。

狙い（重要）
- 初心者がパラメータをいじらなくていい（＝触るのは YES/NO だけ）
- NO_GO の場合でも、システム側が「この方向で直すと良さそう」を提案し、合格した案だけを採用する
- 基準を満たすものだけ稼働（再現性と安全）
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Callable, Dict, List, Tuple

from .judge import JudgeResult, judge_backtest_results


@dataclass
class FixCandidate:
    name: str
    policy: Dict[str, Any]
    judge: JudgeResult


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


def _score_candidate(j: JudgeResult) -> float:
    decision_bonus = 1000.0 if j.decision == "GO" else 0.0
    avg_r = float(j.metrics.get("avg_r", -999))
    max_dd_pct = float(j.metrics.get("max_dd_pct", 9))
    daylimit_days_pct = float(j.metrics.get("daylimit_days_pct", 9))
    # DD / 日次停止の多さは減点（安全寄り）
    return decision_bonus + (avg_r * 120.0) - (max_dd_pct * 60.0) - (daylimit_days_pct * 20.0)


def evaluate_policy(policy: Dict[str, Any], day_results: List[Any], judge_mode: str) -> JudgeResult:
    return judge_backtest_results(day_results, policy, judge_mode=judge_mode)


def _patch_entry_require_value(policy: Dict[str, Any], key: str, new_value: Any) -> bool:
    """
    entry.require が list[dict] 形式のときに、
    例: - near_vwap_pct: 0.2 のような要素を安全に更新する。

    成功したら True、対象がなければ False。
    """
    entry = policy.get("entry", {})
    if not isinstance(entry, dict):
        return False
    req = entry.get("require", None)
    if not isinstance(req, list):
        return False

    changed = False
    for i, item in enumerate(req):
        if not isinstance(item, dict):
            continue
        if key in item:
            item[key] = new_value
            req[i] = item
            changed = True
    entry["require"] = req
    policy["entry"] = entry
    return changed


def auto_fix_policy(
    base_policy: Dict[str, Any],
    day_results_provider: Callable[[Dict[str, Any]], List[Any]],
    max_candidates: int = 12,
    judge_mode: str = "prod",
) -> AutoFixResult:
    """
    base_policy を起点に、修正案を順に試して GO を目指す。

    judge_mode:
      - "dev"  : 開発用しきい値でGOを狙う
      - "prod" : 本番用しきい値でGOを狙う（デフォ）
    """
    # 元ポリシーの評価
    base_day_results = day_results_provider(base_policy)
    base_judge = evaluate_policy(base_policy, base_day_results, judge_mode)

    candidates: List[FixCandidate] = []

    reasons_text = " ".join(list(base_judge.reasons or []))
    is_no_go = (base_judge.decision == "NO_GO")

    # =========================================================
    # 変更候補（単発）
    # =========================================================
    fix_plan: List[Tuple[str, List[Tuple[List[str], Any]], List[Tuple[str, Any]]]] = []
    # edits_dict: (path,value) のリスト（dict用）
    # edits_entry: (entry_key,value) のリスト（entry.require(list)用）

    # 現在値
    tp_now = float(_get_nested(base_policy, ["exit", "take_profit_r"], 1.5) or 1.5)
    mh_now = int(_get_nested(base_policy, ["exit", "max_hold_minutes"], 25) or 25)
    adv_now = float(_get_nested(base_policy, ["exec_guards", "early_stop", "max_adverse_r"], 0.5) or 0.5)
    tl_now = float(_get_nested(base_policy, ["risk", "trade_loss_pct"], 0.003) or 0.003)
    dl_now = float(_get_nested(base_policy, ["risk", "day_loss_pct"], 0.01) or 0.01)
    mtd_now = int(_get_nested(base_policy, ["limits", "max_trades_per_day"], 3) or 3)

    # 1) avg_r が低い（または NO_GO）：期待値を改善したい
    if ("avg_r too low" in reasons_text) or is_no_go:
        # TPを伸ばす（伸ばしすぎると約定しないので段階）
        for tp in [2.0, 2.5, 3.0]:
            if tp > tp_now:
                fix_plan.append(
                    (f"raise_take_profit_r_to_{tp}",
                     [(["exit", "take_profit_r"], tp)],
                     [])
                )

        # ダラダラ負けを減らす：最大保有短縮（25→20→15→12）
        for mh in [20, 15, 12]:
            if mh < mh_now:
                fix_plan.append(
                    (f"reduce_max_hold_minutes_to_{mh}",
                     [(["exit", "max_hold_minutes"], mh)],
                     [])
                )

        # entry.require の near_vwap_pct を少し絞る（0.20→0.18→0.15）
        for nv in [0.18, 0.15]:
            fix_plan.append(
                (f"tighten_near_vwap_pct_to_{nv}",
                 [],
                 [("near_vwap_pct", nv)])
            )

    # 2) DDが大きい：損失制御を強化
    if "max_dd_pct exceeded" in reasons_text or is_no_go:
        # early_stop を強化（0.5→0.45→0.4）
        for adv in [0.45, 0.40]:
            if adv < adv_now:
                fix_plan.append(
                    (f"tighten_early_stop_max_adverse_r_to_{adv}",
                     [(["exec_guards", "early_stop", "enable"], True),
                      (["exec_guards", "early_stop", "max_adverse_r"], adv)],
                     [])
                )

        # 1回の許容損を少し下げる（trade_loss_pct）
        for tl in [max(tl_now * 0.8, 0.0015), max(tl_now * 0.7, 0.0015)]:
            if tl < tl_now:
                fix_plan.append(
                    (f"reduce_trade_loss_pct_to_{round(tl, 4)}",
                     [(["risk", "trade_loss_pct"], float(round(tl, 6)))],
                     [])
                )

        # 1日の許容損を少し下げる（day_loss_pct）
        for dl in [max(dl_now * 0.8, 0.004), max(dl_now * 0.7, 0.004)]:
            if dl < dl_now:
                fix_plan.append(
                    (f"reduce_day_loss_pct_to_{round(dl, 4)}",
                     [(["risk", "day_loss_pct"], float(round(dl, 6)))],
                     [])
                )

    # 3) 連敗が多い：エントリー回数と早期撤退で止血
    if "max_consecutive_losses exceeded" in reasons_text or is_no_go:
        for mt in [2, 1]:
            if mt < mtd_now:
                fix_plan.append(
                    (f"reduce_max_trades_per_day_to_{mt}",
                     [(["limits", "max_trades_per_day"], mt)],
                     [])
                )

        for adv in [0.45, 0.40]:
            if adv < adv_now:
                fix_plan.append(
                    (f"tighten_early_stop_for_consecutive_losses_{adv}",
                     [(["exec_guards", "early_stop", "enable"], True),
                      (["exec_guards", "early_stop", "max_adverse_r"], adv)],
                     [])
                )

    # 4) daylimitが多すぎ：止まりすぎ＝チャンスが潰れてる可能性
    #    ここは「緩める案」と「過剰売買を抑える案」を両方出す（人が選べる）
    if "daylimit_days_pct exceeded" in reasons_text:
        # 緩める案：day_loss_pct を少し上げる（ただし上げすぎない）
        for dl in [min(dl_now * 1.2, 0.02), min(dl_now * 1.35, 0.02)]:
            if dl > dl_now:
                fix_plan.append(
                    (f"increase_day_loss_pct_to_{round(dl, 4)}",
                     [(["risk", "day_loss_pct"], float(round(dl, 6)))],
                     [])
                )
        # 抑える案：max_trades を減らす
        for mt in [2, 1]:
            if mt < mtd_now:
                fix_plan.append(
                    (f"reduce_trades_to_reduce_daylimit_{mt}",
                     [(["limits", "max_trades_per_day"], mt)],
                     [])
                )

    # =========================================================
    # 組み合わせ候補（現実に効きやすいセット）
    # =========================================================
    combo_plan: List[Tuple[str, List[Tuple[List[str], Any]], List[Tuple[str, Any]]]] = []

    # 期待値＋DDの両方を狙うセット
    combo_plan.append(
        ("combo_tp_2.0_hold_15_early_0.45",
         [
             (["exit", "take_profit_r"], 2.0),
             (["exit", "max_hold_minutes"], 15),
             (["exec_guards", "early_stop", "enable"], True),
             (["exec_guards", "early_stop", "max_adverse_r"], min(adv_now, 0.45)),
         ],
         [("near_vwap_pct", 0.18)])
    )

    combo_plan.append(
        ("combo_tp_2.5_hold_12_early_0.40_tradeLossDown",
         [
             (["exit", "take_profit_r"], 2.5),
             (["exit", "max_hold_minutes"], 12),
             (["exec_guards", "early_stop", "enable"], True),
             (["exec_guards", "early_stop", "max_adverse_r"], min(adv_now, 0.40)),
             (["risk", "trade_loss_pct"], float(round(max(tl_now * 0.8, 0.0015), 6))),
         ],
         [("near_vwap_pct", 0.15)])
    )

    # combo を末尾に足す（単発→combo の順で試す）
    fix_plan.extend(combo_plan)

    # =========================================================
    # 候補を順に試す（上限あり）
    # =========================================================
    for name, edits_dict, edits_entry in fix_plan:
        if len(candidates) >= int(max_candidates):
            break

        p2 = deepcopy(base_policy)

        # dictパス変更
        for path, val in edits_dict:
            _set_nested(p2, path, val)

        # entry.require(list)変更
        for k, v in edits_entry:
            _patch_entry_require_value(p2, k, v)

        dr = day_results_provider(p2)
        j = evaluate_policy(p2, dr, judge_mode)
        candidates.append(FixCandidate(name=name, policy=p2, judge=j))

        if j.decision == "GO":
            best = candidates[-1]
            return AutoFixResult(base_judge=base_judge, candidates=candidates, best=best)

    # GOが無ければ「一番マシ」を返す
    if candidates:
        best = max(candidates, key=lambda c: _score_candidate(c.judge))
    else:
        best = FixCandidate(name="base_policy", policy=base_policy, judge=base_judge)

    return AutoFixResult(base_judge=base_judge, candidates=candidates, best=best)