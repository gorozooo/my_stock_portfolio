# aiapp/services/picks_build/hybrid_adjust_service.py
# -*- coding: utf-8 -*-
"""
B側（テクニカル×ファンダ×政策）の “合成” を行うサービス（拡張前提版）。

狙い:
- テクニカルの EV_true_rakuten を “ベース” として尊重
- そこに「ファンダ(0..100)」「政策（セクター別に効き方を変える）」を小さめに混ぜる
- 混ぜた結果を ev_true_rakuten_hybrid に置く（元は保持）
- ログ（理由/寄与内訳）を item に保存し、A/B運用で検証できるようにする

混ぜ方（初期: 安全に小さめ）
- fund_bonus = (fund_score - 50) * 0.04   → だいたい -2 .. +2
- policy_bonus は “政策の中間スコア（fx/risk/rates...）× セクター別weight” を合成して作る
- total_bonus = clamp(fund_bonus + policy_bonus, -6, +6)

重要（今回の修正点）:
- policy_snapshot の sector_rows の値(pr)が dict の場合でも動くようにする（getattr前提を撤去）
- policy_build の components は fx/risk/us_rates/jp_rates なので、
  rates は (us_rates + jp_rates) にフォールバックして作る
- sector_display の文字が崩れても一致できるよう、正規化してから参照する
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import unicodedata

from aiapp.services.fundamentals.repo import load_fund_snapshot
from aiapp.services.policy_build.repo import load_policy_snapshot

from .schema import PickItem


# =========================================================
# Utility（先に置く：import時のテーブル生成で必要）
# =========================================================
def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        if x != x:  # NaN
            return None
        return x
    except Exception:
        return None


def _norm_text(s: Any) -> str:
    """
    文字崩れ/不可視文字（Cf/Ccなど）を除去して比較しやすくする。
    - 例: 輸送用機器 → 輸送用機器
    """
    if s is None:
        return ""
    t = str(s)
    t = unicodedata.normalize("NFKC", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) not in ("Cf", "Cc"))
    return t.strip()


def _get(pr: Any, key: str, default=None):
    """
    policy row(pr) は dict で来ることがある（＝sector_rowsの値がdict）。
    dataclass/obj で来ても動くように “両対応” にする。
    """
    try:
        if isinstance(pr, dict):
            return pr.get(key, default)
        return getattr(pr, key, default)
    except Exception:
        return default


# =========================================================
# 係数（混ぜ方）のテーブル（将来は設定JSON/YAML化もできる）
# =========================================================
COEF: Dict[str, float] = {
    # fund_score -> bonus
    "fund_center": 50.0,
    "fund_k": 0.04,  # (fund_score-50)*0.04  => -2..+2くらい

    # policy_total(mixed score) -> bonus
    "policy_total_k": 0.20,  # -4..+4くらい

    # clamp
    "bonus_clamp_lo": -6.0,
    "bonus_clamp_hi": 6.0,
}


# =========================================================
# セクター別 weight（33業種: まずは default + 代表override）
# ※ keys は “正規化した sector_display”
# =========================================================
JPX33_SECTORS: List[str] = [
    "水産・農林業",
    "鉱業",
    "建設業",
    "食料品",
    "繊維製品",
    "パルプ・紙",
    "化学",
    "医薬品",
    "石油・石炭製品",
    "ゴム製品",
    "ガラス・土石製品",
    "鉄鋼",
    "非鉄金属",
    "金属製品",
    "機械",
    "電気機器",
    "輸送用機器",
    "精密機器",
    "その他製品",
    "電気・ガス業",
    "陸運業",
    "海運業",
    "空運業",
    "倉庫・運輸関連業",
    "情報・通信業",
    "卸売業",
    "小売業",
    "銀行業",
    "証券、商品先物取引業",
    "保険業",
    "その他金融業",
    "不動産業",
    "サービス業",
]


def _default_sector_weights() -> Dict[str, float]:
    # 初期の “無難” なデフォ
    return {
        "fx": 0.2,
        "rates": -0.4,
        "risk": -0.2,
    }


def _build_sector_weight_table() -> Dict[str, Dict[str, float]]:
    table: Dict[str, Dict[str, float]] = {}
    for s in JPX33_SECTORS:
        table[_norm_text(s)] = dict(_default_sector_weights())

    # 代表override（ユーザー指定に寄せる）
    table[_norm_text("輸送用機器")] = {"fx": +1.4, "rates": -0.6, "risk": -0.2}
    table[_norm_text("電気機器")] = {"fx": +1.1, "rates": -0.8, "risk": -0.3}
    table[_norm_text("医薬品")] = {"fx": +0.1, "rates": +0.2, "risk": +0.8}
    table[_norm_text("銀行業")] = {"fx": +0.1, "rates": +1.6, "risk": -0.1}
    table[_norm_text("不動産業")] = {"fx": +0.2, "rates": -1.6, "risk": -0.1}

    # 内需寄りの例（最初は軽く）
    table[_norm_text("小売業")] = {"fx": +0.2, "rates": -0.2, "risk": +0.1}
    table[_norm_text("サービス業")] = {"fx": +0.2, "rates": -0.2, "risk": +0.1}
    table[_norm_text("陸運業")] = {"fx": +0.1, "rates": -0.2, "risk": +0.2}

    return table


# import時に一度だけ生成
SECTOR_WEIGHTS: Dict[str, Dict[str, float]] = _build_sector_weight_table()


def _pick_policy_components(pr: Any) -> Tuple[Optional[float], Optional[Dict[str, float]], List[str]]:
    """
    policy row(pr) から “中間スコア components” を拾う（dict/obj両対応）。
    戻り:
      - policy_score（旧互換の合計スコア）
      - components（fx/risk/us_rates/jp_rates...）なければ None
      - reason_lines（短いログ行）
    """
    policy_score: Optional[float] = None
    components: Optional[Dict[str, float]] = None
    reason_lines: List[str] = []

    # policy_score（dictでも取れるように）
    policy_score = _safe_float(_get(pr, "policy_score", None))

    # flags（短文）
    flags = _get(pr, "flags", None) or []
    if isinstance(flags, list):
        for x in flags[:5]:
            if x:
                reason_lines.append(str(x))

    meta = _get(pr, "meta", None)

    # meta.components（policy_build側: fx/risk/us_rates/jp_rates）
    if isinstance(meta, dict):
        comp = meta.get("components")
        if isinstance(comp, dict) and comp:
            c2: Dict[str, float] = {}
            for k, v in comp.items():
                fv = _safe_float(v)
                if fv is None:
                    continue
                c2[str(k)] = float(fv)
            if c2:
                components = c2

        rs = meta.get("reasons")
        if isinstance(rs, list) and rs:
            for x in rs[:5]:
                if x:
                    reason_lines.append(str(x))

    # 整形
    reason_lines = [x.strip() for x in reason_lines if str(x).strip()]
    if len(reason_lines) > 6:
        reason_lines = reason_lines[:6]

    return policy_score, components, reason_lines


def apply_hybrid_adjust(items: List[PickItem]) -> Dict[str, int]:
    stats: Dict[str, int] = {
        "fund_hit": 0,
        "policy_hit": 0,
        "both_hit": 0,
        "none_hit": 0,
        "policy_components_hit": 0,
        "sector_weight_hit": 0,
        "sector_weight_miss": 0,
    }

    fund_snap = load_fund_snapshot()
    pol_snap = load_policy_snapshot()

    # sector→policy row（正規化して map を作り直す）
    raw_policy_map = pol_snap.sector_rows or {}
    policy_map: Dict[str, Any] = {}
    try:
        for k, v in raw_policy_map.items():
            nk = _norm_text(k)
            if nk:
                policy_map[nk] = v
    except Exception:
        policy_map = raw_policy_map  # 最後の手段

    fund_map = fund_snap.rows or {}

    for it in items:
        code = str(getattr(it, "code", "") or "").strip()
        sec_raw = getattr(it, "sector_display", None)
        sec = _norm_text(sec_raw)

        # -------------------------
        # fund
        # -------------------------
        fund_score: Optional[float] = None
        fund_flags: Optional[List[str]] = None
        if code and code in fund_map:
            fr = fund_map[code]
            fund_score = _safe_float(getattr(fr, "fund_score", None))
            if fund_score is not None:
                fund_score = float(fund_score)

            flags = getattr(fr, "flags", None) or []
            if isinstance(flags, list):
                fund_flags = list(flags)[:10]

            if fund_score is not None:
                stats["fund_hit"] += 1

        # -------------------------
        # policy（sector）
        # -------------------------
        policy_score: Optional[float] = None
        policy_flags: Optional[List[str]] = None
        policy_components: Optional[Dict[str, float]] = None
        policy_reason_lines: List[str] = []

        pr = policy_map.get(sec) if sec else None
        if pr is not None:
            policy_score, policy_components, policy_reason_lines = _pick_policy_components(pr)

            fl = _get(pr, "flags", None) or []
            if isinstance(fl, list):
                policy_flags = list(fl)[:10]

            if policy_score is not None:
                stats["policy_hit"] += 1
            if policy_components is not None:
                stats["policy_components_hit"] += 1

        if fund_score is not None and policy_score is not None:
            stats["both_hit"] += 1
        elif fund_score is None and policy_score is None:
            stats["none_hit"] += 1

        # -------------------------
        # sector weights
        # -------------------------
        w = SECTOR_WEIGHTS.get(sec)
        if w is not None:
            stats["sector_weight_hit"] += 1
        else:
            stats["sector_weight_miss"] += 1
            w = _default_sector_weights()

        # -------------------------
        # fund_bonus
        # -------------------------
        fund_bonus = 0.0
        fund_bonus_used = False
        if fund_score is not None:
            fund_bonus = (float(fund_score) - float(COEF["fund_center"])) * float(COEF["fund_k"])
            fund_bonus_used = True

        # -------------------------
        # policy_bonus
        # -------------------------
        policy_bonus = 0.0
        policy_bonus_used = False
        policy_detail: Dict[str, float] = {}

        if policy_components is not None:
            # policy_build側: fx/risk/us_rates/jp_rates
            fx = float(_safe_float(policy_components.get("fx")) or 0.0)
            risk = float(_safe_float(policy_components.get("risk")) or 0.0)

            # rates はキーが無い可能性があるのでフォールバック:
            # - rates があればそれ
            # - 無ければ us_rates + jp_rates
            rates_v = _safe_float(policy_components.get("rates"))
            if rates_v is None:
                us_rates = float(_safe_float(policy_components.get("us_rates")) or 0.0)
                jp_rates = float(_safe_float(policy_components.get("jp_rates")) or 0.0)
                rates = us_rates + jp_rates
                policy_detail["us_rates"] = us_rates
                policy_detail["jp_rates"] = jp_rates
            else:
                rates = float(rates_v)

            policy_detail["fx"] = fx
            policy_detail["rates"] = rates
            policy_detail["risk"] = risk

            wf = float(_safe_float(w.get("fx")) or 0.0)
            wr = float(_safe_float(w.get("rates")) or 0.0)
            wk = float(_safe_float(w.get("risk")) or 0.0)

            policy_detail["w_fx"] = wf
            policy_detail["w_rates"] = wr
            policy_detail["w_risk"] = wk

            mixed_policy_score = fx * wf + rates * wr + risk * wk
            policy_detail["mixed_policy_score"] = mixed_policy_score

            policy_bonus = mixed_policy_score * float(COEF["policy_total_k"])
            policy_bonus_used = True

        elif policy_score is not None:
            # 旧互換（components が無い場合）
            policy_detail["policy_score"] = float(policy_score)
            policy_detail["mode"] = 0.0  # marker
            policy_bonus = float(policy_score) * float(COEF["policy_total_k"])
            policy_bonus_used = True

        total_bonus = _clamp(
            float(fund_bonus) + float(policy_bonus),
            float(COEF["bonus_clamp_lo"]),
            float(COEF["bonus_clamp_hi"]),
        )

        # -------------------------
        # write back（既存キーは壊さず、追加で保存）
        # -------------------------
        it.fund_score = fund_score
        it.fund_flags = fund_flags

        it.policy_score = policy_score
        it.policy_flags = policy_flags

        it.hybrid_bonus = float(total_bonus) if (fund_bonus_used or policy_bonus_used) else None
        it.hybrid_bonus_total = float(total_bonus) if (fund_bonus_used or policy_bonus_used) else None
        it.hybrid_bonus_fund = float(fund_bonus) if fund_bonus_used else None
        it.hybrid_bonus_policy = float(policy_bonus) if policy_bonus_used else None

        it.hybrid_sector_weights = dict(w) if isinstance(w, dict) else None
        it.hybrid_policy_components = policy_detail if policy_detail else None

        reason_lines: List[str] = []
        if fund_bonus_used:
            reason_lines.append(f"fund_bonus={fund_bonus:.3f} (fund_score={fund_score})")
        if policy_bonus_used:
            if policy_components is not None:
                mps = float(policy_detail.get("mixed_policy_score", 0.0) or 0.0)
                reason_lines.append(
                    f"policy_bonus={policy_bonus:.3f} (mixed_policy_score={mps:.3f} * k={COEF['policy_total_k']})"
                )
            else:
                reason_lines.append(
                    f"policy_bonus={policy_bonus:.3f} (policy_score={policy_score} * k={COEF['policy_total_k']})"
                )

        # policy側の短文も少し（flags/reasons）
        for x in policy_reason_lines[:3]:
            if x:
                reason_lines.append(str(x))

        if sec:
            reason_lines.append(f"sector={sec}")

        reason_lines = [x.strip() for x in reason_lines if str(x).strip()]
        if len(reason_lines) > 8:
            reason_lines = reason_lines[:8]
        it.hybrid_reason_lines = reason_lines if reason_lines else None

        # EV
        base_ev = getattr(it, "ev_true_rakuten", None)
        if base_ev is None:
            it.ev_true_rakuten_hybrid = None
        else:
            try:
                it.ev_true_rakuten_hybrid = float(base_ev) + float(total_bonus)
            except Exception:
                it.ev_true_rakuten_hybrid = None

    return stats