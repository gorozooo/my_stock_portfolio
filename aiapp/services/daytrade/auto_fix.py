# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/auto_fix.py

これは何？
- Judge が NO_GO のときに、policy（active.yml 相当）を自動で改善して
  「GO になる案」を探索する Auto Fixer。

重要ポイント（今回の強化）
- 「1回だけ試して終わる」ではなく、複数段（深さ）で探索する
  例）TP上げる → それでもダメなら時間短縮 → それでもダメならエントリー厳選… のように
- max_candidates / max_depth で安全上限を固定（暴走しない）
- entry.require が list 形式でも安全に扱う（キー指定で値だけ差し替え）

使い方（サービス側）
  fx = auto_fix_policy(base_policy=policy, day_results_provider=_provider, max_candidates=20, judge_mode="dev")
"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Tuple
import json

from .judge import JudgeResult, judge_backtest_results


# =========================
# データ構造
# =========================

@dataclass
class FixCandidate:
    """
    1つの修正案（候補）
    - name: 何をどう変えた案か（ログ用）
    - policy: 修正後 policy
    - judge: その案の JudgeResult
    - chain: どの改善を積んだか（探索の履歴）
    """
    name: str
    policy: Dict[str, Any]
    judge: JudgeResult
    chain: List[str]


@dataclass
class AutoFixResult:
    """
    AutoFix の結果
    - base_judge: 元ポリシーの Judge
    - candidates: 試した候補（順番通り）
    - best: 最も良い候補（GOがあれば最初のGO優先 / なければスコア最大）
    """
    base_judge: JudgeResult
    candidates: List[FixCandidate]
    best: FixCandidate


# =========================
# 汎用ヘルパー
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


def _ensure_dict(d: Dict[str, Any], path: List[str]) -> Dict[str, Any]:
    cur: Any = d
    for k in path:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    return cur


def _canonical_signature(policy: Dict[str, Any]) -> str:
    """
    同一ポリシーを重複評価しないための署名
    """
    try:
        return json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        # 失敗したら id ベース（最悪）
        return str(id(policy))


def _score_candidate(j: JudgeResult) -> float:
    """
    候補の良さを数値化（高いほど良い）
    - GO を最優先（大きなボーナス）
    - avg_r 高いほど良い
    - max_dd_pct 小さいほど良い
    - max_consecutive_losses / daylimit_days_pct も軽く考慮
    """
    decision_bonus = 100000.0 if (j.decision == "GO") else 0.0

    avg_r = float(j.metrics.get("avg_r", -999))
    max_dd_pct = float(j.metrics.get("max_dd_pct", 9))
    max_consecutive_losses = float(j.metrics.get("max_consecutive_losses", 99))
    daylimit_days_pct = float(j.metrics.get("daylimit_days_pct", 1))

    # 適当な重み（開発で調整しやすいように単純）
    return (
        decision_bonus
        + (avg_r * 1000.0)
        - (max_dd_pct * 300.0)
        - (max_consecutive_losses * 10.0)
        - (daylimit_days_pct * 200.0)
    )


def evaluate_policy(policy: Dict[str, Any], day_results: List[Any], judge_mode: str = "prod") -> JudgeResult:
    return judge_backtest_results(day_results, policy, mode=judge_mode)


# =========================
# entry.require(list) を安全に触るヘルパー
# =========================

def _get_entry_require_list(policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    entry = policy.get("entry", {}) or {}
    req = entry.get("require", []) or []
    if isinstance(req, list):
        out = []
        for x in req:
            if isinstance(x, dict) and x:
                out.append(x)
        return out
    return []


def _set_entry_require_value(policy: Dict[str, Any], key: str, value: Any) -> bool:
    """
    entry.require が list のとき：
      - 既に {key: ...} がある → その値を更新
      - 無い → 末尾に追加（安全に）
    return: 変更できたか
    """
    entry = policy.get("entry", None)
    if not isinstance(entry, dict):
        return False
    req = entry.get("require", None)
    if not isinstance(req, list):
        return False

    # 既存を探す
    for item in req:
        if isinstance(item, dict) and (key in item):
            item[key] = value
            return True

    # 無ければ追加
    req.append({key: value})
    return True


def _set_entry_require_range(policy: Dict[str, Any], key: str, lo: float, hi: float) -> bool:
    """
    entry.require の list に {key: [lo, hi]} を安全にセット
    """
    return _set_entry_require_value(policy, key, [float(lo), float(hi)])


# =========================
# 改善アクション定義
# =========================

@dataclass(frozen=True)
class EditAction:
    name: str
    # edits: (path, value) でネストにセット
    edits: List[Tuple[List[str], Any]]
    # entry_list_edits: (key, value) を entry.require(list) に反映
    entry_list_edits: List[Tuple[str, Any]]


def _apply_action(base_policy: Dict[str, Any], act: EditAction) -> Dict[str, Any]:
    p = deepcopy(base_policy)
    for path, val in act.edits:
        _set_nested(p, path, val)
    for k, v in act.entry_list_edits:
        _set_entry_require_value(p, k, v)
    return p


def _build_actions(base_policy: Dict[str, Any], base_judge: JudgeResult) -> List[EditAction]:
    """
    NO_GO原因から、試すべき改善アクションを組み立てる
    """
    reasons = " ".join(list(base_judge.reasons or []))

    actions: List[EditAction] = []

    # 現在値（参考）
    tp_now = float(_get_nested(base_policy, ["exit", "take_profit_r"], 1.5))
    mh_now = int(_get_nested(base_policy, ["exit", "max_hold_minutes"], 25))
    near_now = float(_get_nested(base_policy, ["entry", "near_vwap_pct"], 0.2))  # dict化の将来互換用（現状は未使用）

    # -------------------------
    # avg_r が低い：期待値を上げる方向（利確を伸ばす/エントリー厳選）
    # -------------------------
    if ("avg_r" in reasons) or (base_judge.decision == "NO_GO"):
        # TPを段階的に上げる
        for tp in [2.0, 2.5, 3.0]:
            if tp > tp_now:
                actions.append(EditAction(
                    name=f"raise_take_profit_r_to_{tp}",
                    edits=[(["exit", "take_profit_r"], float(tp))],
                    entry_list_edits=[],
                ))

        # 時間切れを短くしてダラ負けを減らす
        for mh in [20, 15, 12, 10, 8]:
            if mh < mh_now:
                actions.append(EditAction(
                    name=f"reduce_max_hold_minutes_to_{mh}",
                    edits=[(["exit", "max_hold_minutes"], int(mh))],
                    entry_list_edits=[],
                ))

        # エントリー厳選：pullback_pct_range を狭める（リバーサル確認を強める）
        # 例: [0.3,0.8] → [0.35,0.7] → [0.4,0.65]
        actions.append(EditAction(
            name="tighten_pullback_range_035_070",
            edits=[],
            entry_list_edits=[("pullback_pct_range", [0.35, 0.70])],
        ))
        actions.append(EditAction(
            name="tighten_pullback_range_040_065",
            edits=[],
            entry_list_edits=[("pullback_pct_range", [0.40, 0.65])],
        ))

        # volume_increase を true に（勢い確認）
        actions.append(EditAction(
            name="require_volume_increase_true",
            edits=[],
            entry_list_edits=[("volume_increase", True)],
        ))

        # near_vwap_pct をきつく（dict化/将来互換：今のYAMLがdictなら効く）
        # ※現状YAMLが entry.require(list) に near_vwap_pct を持つならそっちを上書きする
        actions.append(EditAction(
            name="tighten_near_vwap_pct_to_015",
            edits=[(["entry", "near_vwap_pct"], 0.15)],
            entry_list_edits=[("near_vwap_pct", 0.15)],
        ))
        actions.append(EditAction(
            name="tighten_near_vwap_pct_to_012",
            edits=[(["entry", "near_vwap_pct"], 0.12)],
            entry_list_edits=[("near_vwap_pct", 0.12)],
        ))

    # -------------------------
    # DD / 連敗がきつい：守りを強める（早期撤退・時間短縮）
    # -------------------------
    if ("max_dd_pct" in reasons) or ("max_consecutive_losses" in reasons) or ("daylimit_days_pct" in reasons):
        # early_stop を強める
        actions.append(EditAction(
            name="enable_early_stop_and_set_max_adverse_r_04",
            edits=[
                (["exec_guards", "early_stop", "enable"], True),
                (["exec_guards", "early_stop", "max_adverse_r"], 0.4),
            ],
            entry_list_edits=[],
        ))
        actions.append(EditAction(
            name="enable_early_stop_and_set_max_adverse_r_03",
            edits=[
                (["exec_guards", "early_stop", "enable"], True),
                (["exec_guards", "early_stop", "max_adverse_r"], 0.3),
            ],
            entry_list_edits=[],
        ))

        # vwap_exit_grace を短縮（逆行時の粘りを減らす）
        actions.append(EditAction(
            name="reduce_vwap_exit_grace_minutes_to_3",
            edits=[
                (["exit", "vwap_exit_grace", "enable"], True),
                (["exit", "vwap_exit_grace", "grace_minutes_after_entry"], 3),
            ],
            entry_list_edits=[],
        ))

        # max_hold_minutes も短縮を重ねやすいよう追加
        for mh in [15, 12, 10]:
            if mh < mh_now:
                actions.append(EditAction(
                    name=f"reduce_max_hold_minutes_to_{mh}_for_dd",
                    edits=[(["exit", "max_hold_minutes"], int(mh))],
                    entry_list_edits=[],
                ))

    # -------------------------
    # 最低限：安全ネット（データが薄い/荒い時）
    # -------------------------
    actions.append(EditAction(
        name="raise_min_stop_yen_to_3",
        edits=[(["risk", "min_stop_yen"], 3)],
        entry_list_edits=[],
    ))
    actions.append(EditAction(
        name="raise_min_stop_yen_to_5",
        edits=[(["risk", "min_stop_yen"], 5)],
        entry_list_edits=[],
    ))

    # 重複排除（name基準）
    uniq: List[EditAction] = []
    seen = set()
    for a in actions:
        if a.name in seen:
            continue
        seen.add(a.name)
        uniq.append(a)
    return uniq


# =========================
# メイン：探索型 AutoFix
# =========================

def auto_fix_policy(
    base_policy: Dict[str, Any],
    day_results_provider: Callable[[Dict[str, Any]], List[Any]],
    max_candidates: int = 20,
    judge_mode: str = "prod",
    max_depth: int = 3,
) -> AutoFixResult:
    """
    base_policy を起点に、改善アクションを積み上げて探索し、GO を狙う。

    - max_candidates: 評価する候補数の上限（安全装置）
    - max_depth: 改善を何段まで積むか（例: 3 なら最大3回変更を組み合わせる）

    探索方針（わかりやすさ優先）
    - まず単発（depth=1）
    - ダメなら 2手組み合わせ（depth=2）
    - それでもダメなら 3手（depth=3）
    - 上限に達したら最良案を返す
    """
    # ---- base judge ----
    base_day_results = day_results_provider(base_policy)
    base_judge = evaluate_policy(base_policy, base_day_results, judge_mode=judge_mode)

    candidates: List[FixCandidate] = []

    # まずは原因に応じたアクションを作る
    actions = _build_actions(base_policy, base_judge)

    # 署名で重複評価を防ぐ
    visited = set()
    visited.add(_canonical_signature(base_policy))

    # best 初期値は base
    best = FixCandidate(name="base_policy", policy=base_policy, judge=base_judge, chain=["base"])

    # GOなら即返す
    if base_judge.decision == "GO":
        return AutoFixResult(base_judge=base_judge, candidates=[], best=best)

    # 探索キュー：（policy, chain_names）
    # depthごとに “分かりやすい順” を維持したいので、BFSっぽく回す
    frontier: List[Tuple[Dict[str, Any], List[str]]] = [(base_policy, [])]

    # ---- depth loop ----
    for depth in range(1, int(max_depth) + 1):
        next_frontier: List[Tuple[Dict[str, Any], List[str]]] = []

        for p_cur, chain in frontier:
            for act in actions:
                if len(candidates) >= int(max_candidates):
                    # 上限到達
                    break

                # 同じアクションを同じチェーンで何度も積まない（無限ループ防止）
                if act.name in chain:
                    continue

                p2 = _apply_action(p_cur, act)
                sig = _canonical_signature(p2)
                if sig in visited:
                    continue
                visited.add(sig)

                dr = day_results_provider(p2)
                j = evaluate_policy(p2, dr, judge_mode=judge_mode)

                name = "+".join(chain + [act.name]) if chain else act.name
                cand = FixCandidate(
                    name=name,
                    policy=p2,
                    judge=j,
                    chain=chain + [act.name],
                )
                candidates.append(cand)

                # best更新（GO優先 / スコア）
                if j.decision == "GO":
                    # “最初にGOになった案” を即返す（時間を無駄にしない）
                    return AutoFixResult(base_judge=base_judge, candidates=candidates, best=cand)

                if _score_candidate(j) > _score_candidate(best.judge):
                    best = cand

                # 次段へ展開
                next_frontier.append((p2, chain + [act.name]))

            if len(candidates) >= int(max_candidates):
                break

        frontier = next_frontier
        if len(candidates) >= int(max_candidates):
            break
        if not frontier:
            break

    # GO無し → 最良案
    if candidates:
        best2 = max(candidates, key=lambda c: _score_candidate(c.judge))
        # baseより悪い場合もあり得るが、best初期がbaseなので比較して良い方に
        if _score_candidate(best2.judge) > _score_candidate(best.judge):
            best = best2

    return AutoFixResult(base_judge=base_judge, candidates=candidates, best=best)