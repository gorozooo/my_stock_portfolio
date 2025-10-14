# portfolio/services/learner.py
from __future__ import annotations
from typing import Dict, Tuple
from django.utils import timezone
from ..models_advisor import AdviceSession, AdviceItem, AdvicePolicy

# ===== 報酬定義（0〜1に正規化気味） =====
def _reward(before: Dict, after: Dict) -> float:
    """
    “良い変化”にプラス: ROI↑, 流動性↑, 信用比率↓
    ざっくり重み付け（必要なら後で学習化）
    """
    def g(key, d, default=0.0): 
        v = d.get(key)
        return float(v if v is not None else default)

    d_roi  = (g("roi_eval_pct", after) - g("roi_eval_pct", before)) / 40.0     # ±1.0/40pt
    d_liq  = (g("liquidity_rate_pct", after) - g("liquidity_rate_pct", before)) / 50.0
    d_mg   = (g("margin_ratio_pct", before) - g("margin_ratio_pct", after)) / 50.0
    r = max(-1.0, min(1.0, 0.5*d_roi + 0.3*d_liq + 0.2*d_mg))
    return (r + 1.0) / 2.0  # 0〜1 に寄せる

# ===== 成果集計 → ポリシー更新 =====
def learn_from_outcomes(horizon_days: int = 14) -> int:
    """
    horizon_days 経過した “taken=True かつ outcome未計算” の AdviceItem を評価し、
    AdvicePolicy を更新する。戻り値は更新件数。
    """
    now = timezone.now().date()
    cnt = 0
    # 直近のセッションと現在KPIの比較は、呼び出し側で after を渡すのが理想だが、
    # まずは簡易：同種の最新セッションを "after" として使う。
    latest = AdviceSession.objects.order_by("-created_at").first()
    if not latest:
        return 0

    # horizon 経過した “taken” のアドバイスを拾う
    items = AdviceItem.objects.filter(
        taken=True, outcome__isnull=True,
        session__created_at__date__lte=now - timezone.timedelta(days=horizon_days)
    ).select_related("session")

    for it in items:
        before = it.session.context_json or {}
        after  = latest.context_json or {}
        r = _reward(before, after)

        # outcome保存
        it.outcome = {"reward": r, "before": before, "after": after}
        it.save(update_fields=["outcome"])

        # ポリシー更新（逐次平均）
        pol, _ = AdvicePolicy.objects.get_or_create(kind=it.kind)
        pol.n += 1
        pol.total_reward += float(r)
        pol.avg_reward = pol.total_reward / max(1, pol.n)
        pol.save(update_fields=["n", "total_reward", "avg_reward", "updated_at"])
        cnt += 1

    return cnt