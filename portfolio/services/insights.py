# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from django.utils import timezone

from ..models_advisor import AdviceSession

# ===== ユーティリティ =====
def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _improve_between(k0: Dict, k1: Dict) -> Dict[str, float]:
    """
    KPIの改善スコアを構成要素込みで返す。
    改善方向：
      - roi_eval_pct: ↑が◎
      - liquidity_rate_pct: ↑が◎
      - margin_ratio_pct: ↓が◎（逆符号）
    """
    if not k0 or not k1:
        return {"score": 0.0, "d_roi": 0.0, "d_liq": 0.0, "d_mrg": 0.0}

    d_roi = _safe_float(k1.get("roi_eval_pct")) - _safe_float(k0.get("roi_eval_pct"))
    d_liq = _safe_float(k1.get("liquidity_rate_pct")) - _safe_float(k0.get("liquidity_rate_pct"))
    d_mrg = _safe_float(k0.get("margin_ratio_pct")) - _safe_float(k1.get("margin_ratio_pct"))  # 低いほど◎

    # ざっくり正規化（±50/±40/±40 を ±1.0 とみなす）
    def clip(x, s): return max(-1.0, min(1.0, x / s)) if s else 0.0
    roi_norm = clip(d_roi, 50.0)
    liq_norm = clip(d_liq, 40.0)
    mrg_norm = clip(d_mrg, 40.0)
    score = (roi_norm + liq_norm + mrg_norm) / 3.0

    return dict(score=score, d_roi=d_roi, d_liq=d_liq, d_mrg=-d_mrg)  # d_mrgは見やすく“増減”で返す

@dataclass
class Insight:
    label: str     # 表示ラベル
    sign: int      # +1 改善と正相関 / -1 逆相関
    contrib: float # 寄与の大きさ（尺度付き）
    sample: str    # 代表例（任意）

# ===== 本体 =====
def generate_insights(horizon_days: int = 7, since_days: int = 90, top_k: int = 3) -> Tuple[str, List[str]]:
    """
    過去の AdviceSession を走査し、「どの要因（流動性↑/信用↓/ROI↑ 等）が
    改善スコアと相関していたか」を簡易推定し、日本語で箇条書きを返す。

    戻り値:
      title: 1行サマリ
      bullets: 箇条書き（MAX top_k）
    """
    cutoff = timezone.now() - timedelta(days=since_days)
    sessions: List[AdviceSession] = list(AdviceSession.objects.filter(created_at__gte=cutoff).order_by("created_at"))
    if len(sessions) < 2:
        return "改善要因の分析にはデータが不足しています。", []

    # horizon 後のセッションを探す
    def find_future(idx: int):
        base = sessions[idx]
        target = base.created_at + timedelta(days=horizon_days)
        for j in range(idx + 1, len(sessions)):
            if sessions[j].created_at >= target:
                return sessions[j]
        return None

    # 共分散っぽい簡易寄与（Δ×改善スコア）を積算
    agg = {
        "roi_up": {"sum": 0.0, "n": 0, "best": None},   # ROI_eval↑
        "liq_up": {"sum": 0.0, "n": 0, "best": None},   # 流動性↑
        "mrg_dn": {"sum": 0.0, "n": 0, "best": None},   # 信用比率↓
    }

    def _update_best(slot: dict, contrib: float, s0: AdviceSession, s1: AdviceSession, delta_fmt: str):
        if slot["best"] is None or abs(contrib) > abs(slot["best"][0]):
            slot["best"] = (contrib, f"{s0.created_at:%Y-%m-%d}→{s1.created_at:%Y-%m-%d}（{delta_fmt}）")

    for i, s0 in enumerate(sessions):
        s1 = find_future(i)
        if not s1:
            continue
        k0 = s0.context_json or {}
        k1 = s1.context_json or {}
        res = _improve_between(k0, k1)
        score = float(res["score"])
        # 各要因のΔ（改善方向の符号でそのまま掛ける）
        roi_d = float(res["d_roi"])  # ↑で◎
        liq_d = float(res["d_liq"])  # ↑で◎
        mrg_d = float(res["d_mrg"])  # ↑（=“比率が増”）は×、なので後で符号反転して寄与

        # 寄与っぽい指標（Δ×スコア）
        c_roi = roi_d * score
        c_liq = liq_d * score
        c_mrg = (-mrg_d) * score  # 信用“減”が◎ → Δ負が改善寄与なので符号反転

        for key, c, delta_fmt in (
            ("roi_up", c_roi, f"ROI {roi_d:+.1f}pt"),
            ("liq_up", c_liq, f"流動性 {liq_d:+.1f}pt"),
            ("mrg_dn", c_mrg, f"信用比率 {mrg_d:+.1f}pt"),
        ):
            agg[key]["sum"] += c
            agg[key]["n"] += 1
            _update_best(agg[key], c, s0, s1, delta_fmt)

    insights: List[Insight] = []
    mapping = {
        "roi_up": ("評価ROIの上昇", +1),
        "liq_up": ("流動性の改善（現金比率↑）", +1),
        "mrg_dn": ("信用比率の低下（レバレッジ圧縮）", +1),
    }
    for k, v in agg.items():
        n = v["n"]
        if n <= 0:
            continue
        avg = v["sum"] / max(1, n)
        label, sign = mapping[k]
        best = v["best"][1] if v.get("best") else ""
        insights.append(Insight(label=label, sign=sign, contrib=avg, sample=best))

    # 寄与の大きい順（絶対値）で上位だけ採用
    insights.sort(key=lambda x: abs(x.contrib), reverse=True)
    top = insights[:top_k]

    # 見出し
    if not top:
        return "直近では顕著な改善要因は見つかりませんでした。", []

    title = "直近の改善に寄与した要因（推定）"
    bullets = []
    for it in top:
        arrow = "↑" if it.sign > 0 else "↓"
        bullets.append(f"・{it.label} が改善スコアと相関（寄与 {it.contrib:+.3f}）。例: {it.sample}")
    return title, bullets