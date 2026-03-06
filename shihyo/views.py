"""
[FILE] views.py
[PATH] <project_root>/shihyo/views.py

このファイルは何？
- 指標専用ダッシュボード（iPhone向け1画面）を表示するViewです。
- 最新スナップショットを1件だけ表示します。
- 最新スナップショットの実データから、
  1) リスクメーター用の score / title / needle角度
  2) AI予想カード用の label / 予想値 / 乖離率 / 根拠
  を計算してテンプレートへ渡します。
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from shihyo.models import MarketIndicatorSnapshot


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _format_price(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _build_risk_context(latest: MarketIndicatorSnapshot) -> dict:
    nikkei_pct = float(latest.nikkei_futures_change_pct or 0.0)
    fx_pct = float(latest.usdjpy_change_pct or 0.0)
    vix_last = float(latest.vix_last or 0.0)
    vix_pct = float(latest.vix_change_pct or 0.0)

    # 0=攻め / 100=守り の実スコア
    score = 50.0

    # 日経先物が弱いほどリスク加算、強いほど減算
    score += max(0.0, -nikkei_pct) * 10.0
    score -= max(0.0, nikkei_pct) * 8.0

    # ドル円が円高方向ならリスク加算、円安方向なら減算
    score += max(0.0, -fx_pct) * 20.0
    score -= max(0.0, fx_pct) * 12.0

    # VIXの水準が高いほどリスク加算
    if vix_last > 15:
        score += (vix_last - 15.0) * 1.7

    # VIX上昇はリスク加算、VIX低下は減算
    score += max(0.0, vix_pct) * 0.6
    score -= max(0.0, -vix_pct) * 0.35

    # 既存action_titleも少しだけ反映
    action_title = latest.action_title or ""
    if "守る" in action_title:
        score += 8.0
    elif "様子見" in action_title:
        score += 0.0
    else:
        score -= 8.0

    score = int(round(_clamp(score, 0.0, 100.0)))

    # 針角度：0=左, 50=中央, 100=右
    needle_deg = -58.0 + (score / 100.0) * 116.0
    needle_deg = round(needle_deg, 1)

    if score >= 67:
        title = "今は守る相場"
        badge_class = "risk-badge-red"
    elif score >= 34:
        title = "今は様子見"
        badge_class = "risk-badge-yellow"
    else:
        title = "今は攻める相場"
        badge_class = "risk-badge-green"

    lines = latest.action_lines or []
    if isinstance(lines, list) and lines:
        summary = " / ".join([str(x) for x in lines[:2]])
    else:
        summary = "指標から総合判定を計算中"

    return {
        "score": score,
        "needle_deg": needle_deg,
        "title": title,
        "badge_class": badge_class,
        "summary": summary,
    }


def _build_ai_prediction_context(latest: MarketIndicatorSnapshot, risk: dict) -> dict:
    nikkei_last = float(latest.nikkei_futures_last or 0.0)
    nikkei_pct = float(latest.nikkei_futures_change_pct or 0.0)
    fx_pct = float(latest.usdjpy_change_pct or 0.0)
    vix_last = float(latest.vix_last or 0.0)
    vix_pct = float(latest.vix_change_pct or 0.0)
    risk_score = int(risk["score"])

    # 実データから短期シグナルを作る
    signal = 0.0
    signal += nikkei_pct * 1.15
    signal += fx_pct * 3.20
    signal += (-vix_pct) * 0.18

    # VIX水準が高いときは慎重寄り
    if vix_last > 20:
        signal -= (vix_last - 20.0) * 0.18
    elif 0 < vix_last < 15:
        signal += (15.0 - vix_last) * 0.08

    # リスクスコアも補正に使う
    if risk_score >= 70:
        signal -= 0.8
    elif risk_score <= 30:
        signal += 0.5

    # 乖離率（予想値 - 現在値）
    gap_pct_value = _clamp(signal * 0.55, -2.80, 2.80)

    if gap_pct_value >= 0.35:
        label = "上昇予想"
        badge_class = "ai-pred-badge ai-pred-badge-up"
        gap_class = "ai-main-value-up"
    elif gap_pct_value <= -0.35:
        label = "下落予想"
        badge_class = "ai-pred-badge ai-pred-badge-down"
        gap_class = "ai-main-value-down"
    else:
        label = "様子見"
        badge_class = "ai-pred-badge ai-pred-badge-wait"
        gap_class = "ai-main-value-wait"

    predicted_value = nikkei_last * (1.0 + (gap_pct_value / 100.0))

    # 簡易信頼度
    confidence_value = 52 + min(28, int(abs(signal) * 8))
    confidence_value = int(_clamp(confidence_value, 50, 80))

    reasons: list[str] = []

    if nikkei_pct > 0.3:
        reasons.append("日経先物が上向き")
    elif nikkei_pct < -0.3:
        reasons.append("日経先物が重い")

    if fx_pct > 0.15:
        reasons.append("ドル円が追い風")
    elif fx_pct < -0.15:
        reasons.append("円高が重し")

    if vix_pct < -3.0:
        reasons.append("VIX低下で警戒後退")
    elif vix_pct > 3.0:
        reasons.append("VIX上昇で警戒増加")

    if vix_last >= 25:
        reasons.append("VIX水準が高く不安定")
    elif 0 < vix_last < 18:
        reasons.append("VIX水準は比較的落ち着き")

    if not reasons:
        reasons.append("主要3指標は中立圏")

    reason_text = " / ".join(reasons[:3])

    return {
        "subtitle": "日経先物ベース短期シナリオ",
        "label": label,
        "badge_class": badge_class,
        "predicted_value": _format_price(predicted_value, 1),
        "current_value": _format_price(nikkei_last, 1),
        "gap_pct": f"{gap_pct_value:+.2f}%",
        "gap_class": gap_class,
        "confidence": f"{confidence_value}%",
        "updated_at": latest.created_at.strftime("%H:%M") if latest.created_at else "-",
        "reason": reason_text,
    }


@login_required
def dashboard(request):
    latest = MarketIndicatorSnapshot.objects.first()

    context = {
        "latest": latest,
        "risk": None,
        "ai_pred": None,
    }

    if latest:
        risk = _build_risk_context(latest)
        ai_pred = _build_ai_prediction_context(latest, risk)
        context["risk"] = risk
        context["ai_pred"] = ai_pred

    return render(request, "shihyo/dashboard.html", context)