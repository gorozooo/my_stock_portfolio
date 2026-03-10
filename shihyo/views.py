"""
[FILE] views.py
[PATH] <project_root>/shihyo/views.py

このファイルは何？
- 指標専用ダッシュボード（iPhone向け1画面）を表示するViewです。
- 最新スナップショットを1件だけ表示します。
- 最新スナップショットの実データから、
  1) リスクメーター用の score / title / needle角度
  2) 上段4カード用の表示データ
  3) 日経平均AI予想カード用の label / 予想値 / 根拠
  4) 市場の偏りカード用の表示データ
  を計算してテンプレートへ渡します。

今回の修正ポイント：
- 上段4カードの表示をすべて views.py 側で整形して統一
- 日経先物 / 日経 は小数点以下2桁で表示
- 3桁カンマ区切りも統一
"""

from __future__ import annotations

from datetime import time as dt_time

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from shihyo.models import MarketIndicatorSnapshot, ShihyoMarketBiasSnapshot


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _format_price(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_signed_number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    if value > 0:
        return f"+{value:,.{digits}f}"
    return f"{value:,.{digits}f}"


def _format_signed_percent(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    if value > 0:
        return f"+{value:.{digits}f}%"
    return f"{value:.{digits}f}%"


def _delta_class(value: float | None, vix_mode: bool = False) -> str:
    if value is None:
        return "change-flat"

    if not vix_mode:
        if value > 0:
            return "change-up"
        if value < 0:
            return "change-down"
        return "change-flat"

    if value > 0:
        return "vix-change-up"
    if value < 0:
        return "vix-change-down"
    return "change-flat"


def _build_card_delta_display(change_value: float | None, pct_value: float | None, digits: int = 2) -> str:
    if change_value is None or pct_value is None:
        return "-"
    return f"{_format_signed_number(change_value, digits)} ({_format_signed_percent(pct_value, 2)})"


def _extract_nikkei_spot(raw_payload: dict | None) -> tuple[float, float, float, float, bool, str]:
    """
    raw_payload から日経平均（現物）の close / change / change_pct / previous_close を取り出す。
    戻り値:
      (close, change, change_pct, previous_close, available, source_name)
    """
    if not isinstance(raw_payload, dict):
        return 0.0, 0.0, 0.0, 0.0, False, "unknown"

    nikkei_spot = raw_payload.get("nikkei_spot") or {}
    available = bool(nikkei_spot.get("available"))
    source_name = str(nikkei_spot.get("source") or "unknown")

    close_value = _safe_float(nikkei_spot.get("close"), 0.0)
    calc_dict = nikkei_spot.get("calc") or {}
    change_value = _safe_float(calc_dict.get("change"), 0.0)
    change_pct = _safe_float(calc_dict.get("pct"), 0.0)
    previous_close = _safe_float(nikkei_spot.get("previous_close"), 0.0)

    if not available or close_value <= 0:
        return 0.0, 0.0, 0.0, 0.0, False, source_name

    return close_value, change_value, change_pct, previous_close, True, source_name


def _build_risk_context(latest: MarketIndicatorSnapshot) -> dict:
    nikkei_pct = _safe_float(latest.nikkei_futures_change_pct, 0.0)
    fx_pct = _safe_float(latest.usdjpy_change_pct, 0.0)
    vix_last = _safe_float(latest.vix_last, 0.0)
    vix_pct = _safe_float(latest.vix_change_pct, 0.0)

    score = 50.0
    score += max(0.0, -nikkei_pct) * 10.0
    score -= max(0.0, nikkei_pct) * 8.0

    score += max(0.0, -fx_pct) * 20.0
    score -= max(0.0, fx_pct) * 12.0

    if vix_last > 15:
        score += (vix_last - 15.0) * 1.7

    score += max(0.0, vix_pct) * 0.6
    score -= max(0.0, -vix_pct) * 0.35

    action_title = latest.action_title or ""
    if "守る" in action_title:
        score += 8.0
    elif "様子見" in action_title:
        score += 0.0
    else:
        score -= 8.0

    score = int(round(_clamp(score, 0.0, 100.0)))
    needle_deg = round(-58.0 + (score / 100.0) * 116.0, 1)

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


def _build_futures_card_context(latest: MarketIndicatorSnapshot) -> dict:
    change_value = latest.nikkei_futures_change
    pct_value = latest.nikkei_futures_change_pct

    return {
        "name": "日経先物",
        "value": _format_price(latest.nikkei_futures_last, 2),
        "delta_display": _build_card_delta_display(change_value, pct_value, digits=2),
        "delta_class": _delta_class(change_value),
        "label": latest.nikkei_label or "中立",
        "label_class": "mini-label-red",
    }


def _build_fx_card_context(latest: MarketIndicatorSnapshot) -> dict:
    change_value = latest.usdjpy_change
    pct_value = latest.usdjpy_change_pct

    return {
        "name": "ドル円",
        "value": _format_price(latest.usdjpy_last, 3),
        "delta_display": _build_card_delta_display(change_value, pct_value, digits=3),
        "delta_class": _delta_class(change_value),
        "label": latest.fx_label or "中立",
        "label_class": "mini-label-red",
    }


def _build_vix_card_context(latest: MarketIndicatorSnapshot) -> dict:
    change_value = latest.vix_change
    pct_value = latest.vix_change_pct

    if pct_value is not None and pct_value > 0:
        label_class = "mini-label-yellow"
    elif pct_value is not None and pct_value < 0:
        label_class = "mini-label-blue"
    else:
        label_class = "mini-label-gray"

    return {
        "name": "VIX",
        "value": _format_price(latest.vix_last, 2),
        "delta_display": _build_card_delta_display(change_value, pct_value, digits=2),
        "delta_class": _delta_class(change_value, vix_mode=True),
        "label": latest.vix_label or "中立",
        "label_class": label_class,
    }


def _build_nikkei_spot_card_context(latest: MarketIndicatorSnapshot) -> dict:
    nikkei_spot_last, nikkei_spot_change, nikkei_spot_pct, raw_previous_close, spot_available, spot_source = _extract_nikkei_spot(latest.raw_payload)

    if not spot_available or nikkei_spot_last <= 0:
        return {
            "name": "日経",
            "value": "-",
            "delta_display": "-",
            "delta_class": "change-flat",
            "label": "取得待ち",
            "label_class": "mini-label-gray",
            "source": spot_source,
        }

    reference_close = _safe_float(latest.nikkei_spot_previous_close, 0.0)
    if reference_close <= 0:
        reference_close = raw_previous_close

    if reference_close > 0:
        spot_change = nikkei_spot_last - reference_close
        spot_pct = ((nikkei_spot_last / reference_close) - 1.0) * 100.0
    else:
        spot_change = nikkei_spot_change
        spot_pct = nikkei_spot_pct

    if spot_pct >= 1.0:
        label = "強い"
        label_class = "mini-label-green"
    elif spot_pct >= 0.2:
        label = "やや強い"
        label_class = "mini-label-green"
    elif spot_pct <= -1.0:
        label = "弱い"
        label_class = "mini-label-softred"
    elif spot_pct <= -0.2:
        label = "やや弱い"
        label_class = "mini-label-softred"
    else:
        label = "中立"
        label_class = "mini-label-gray"

    return {
        "name": "日経",
        "value": _format_price(nikkei_spot_last, 2),
        "delta_display": _build_card_delta_display(spot_change, spot_pct, digits=2),
        "delta_class": _delta_class(spot_change),
        "label": label,
        "label_class": label_class,
        "source": spot_source,
    }


def _build_ai_prediction_context(latest: MarketIndicatorSnapshot, risk: dict) -> dict:
    (
        nikkei_spot_last,
        nikkei_spot_change,
        nikkei_spot_pct,
        raw_previous_close,
        spot_available,
        spot_source,
    ) = _extract_nikkei_spot(latest.raw_payload)

    nikkei_futures_pct = _safe_float(latest.nikkei_futures_change_pct, 0.0)
    fx_pct = _safe_float(latest.usdjpy_change_pct, 0.0)
    vix_last = _safe_float(latest.vix_last, 0.0)
    vix_pct = _safe_float(latest.vix_change_pct, 0.0)
    risk_score = int(risk["score"])

    if not spot_available or nikkei_spot_last <= 0:
        return {
            "subtitle": "日経平均ベース短期シナリオ",
            "label": "取得待ち",
            "badge_class": "ai-pred-badge ai-pred-badge-wait",
            "predicted_value": "-",
            "current_value": "-",
            "gap_pct": "-",
            "confidence": "-",
            "updated_at": latest.created_at.strftime("%H:%M") if latest.created_at else "-",
            "reason": f"日経平均現物が取得できないため予想を停止中（source={spot_source}）。先物では代用しません。",
            "predicted_delta_display": "-",
            "predicted_delta_class": "change-flat",
            "current_delta_display": "-",
            "current_delta_class": "change-flat",
        }

    signal = 0.0
    signal += nikkei_spot_pct * 1.35
    signal += nikkei_futures_pct * 1.60
    signal += fx_pct * 3.20
    signal += (-vix_pct) * 0.18

    if vix_last > 20:
        signal -= (vix_last - 20.0) * 0.18
    elif 0 < vix_last < 15:
        signal += (15.0 - vix_last) * 0.08

    if risk_score >= 70:
        signal -= 0.8
    elif risk_score <= 30:
        signal += 0.5

    gap_pct_value = _clamp(signal * 0.55, -2.80, 2.80)

    if gap_pct_value >= 0.35:
        label = "上昇予想"
        badge_class = "ai-pred-badge ai-pred-badge-up"
    elif gap_pct_value <= -0.35:
        label = "下落予想"
        badge_class = "ai-pred-badge ai-pred-badge-down"
    else:
        label = "様子見"
        badge_class = "ai-pred-badge ai-pred-badge-wait"

    predicted_value = nikkei_spot_last * (1.0 + (gap_pct_value / 100.0))

    reference_close = _safe_float(latest.nikkei_spot_previous_close, 0.0)
    if reference_close <= 0:
        reference_close = raw_previous_close

    if reference_close <= 0 and nikkei_spot_last > 0 and nikkei_spot_change != 0:
        candidate = nikkei_spot_last - nikkei_spot_change
        if candidate > 0:
            reference_close = candidate

    if reference_close > 0:
        predicted_delta_abs = predicted_value - reference_close
        predicted_delta_pct = ((predicted_value / reference_close) - 1.0) * 100.0
    else:
        predicted_delta_abs = None
        predicted_delta_pct = None

    confidence_value = 52 + min(28, int(abs(signal) * 8))
    confidence_value = int(_clamp(confidence_value, 50, 80))

    reasons: list[str] = []

    if nikkei_spot_pct > 0.2:
        reasons.append("日経平均が堅調")
    elif nikkei_spot_pct < -0.2:
        reasons.append("日経平均が重い")

    if nikkei_futures_pct > 0.3:
        reasons.append("先物が先行して上向き")
    elif nikkei_futures_pct < -0.3:
        reasons.append("先物が先行して弱い")

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
        reasons.append("主要指標は中立圏")

    reason_text = " / ".join(reasons[:3])

    if predicted_delta_abs is None or predicted_delta_pct is None:
        predicted_delta_display = "-"
        predicted_delta_class = "change-flat"
    else:
        predicted_delta_display = (
            f"{_format_signed_number(predicted_delta_abs, 1)} "
            f"({_format_signed_percent(predicted_delta_pct, 2)})"
        )
        predicted_delta_class = _delta_class(predicted_delta_abs)

    return {
        "subtitle": "日経平均ベース短期シナリオ",
        "label": label,
        "badge_class": badge_class,
        "predicted_value": _format_price(predicted_value, 1),
        "current_value": _format_price(nikkei_spot_last, 1),
        "gap_pct": _format_signed_percent(gap_pct_value, 2),
        "confidence": f"{confidence_value}%",
        "updated_at": latest.created_at.strftime("%H:%M") if latest.created_at else "-",
        "reason": reason_text,
        "predicted_delta_display": predicted_delta_display,
        "predicted_delta_class": predicted_delta_class,
        "current_delta_display": "-",
        "current_delta_class": "change-flat",
    }


def _select_market_bias_snapshot() -> ShihyoMarketBiasSnapshot | None:
    now_local = timezone.localtime()
    today_local = now_local.date()
    current_time = now_local.time()

    today_preopen = (
        ShihyoMarketBiasSnapshot.objects
        .filter(date=today_local, mode=ShihyoMarketBiasSnapshot.MODE_PREOPEN)
        .order_by("-updated_at")
        .first()
    )
    today_open_1000 = (
        ShihyoMarketBiasSnapshot.objects
        .filter(date=today_local, mode=ShihyoMarketBiasSnapshot.MODE_OPEN_1000)
        .order_by("-updated_at")
        .first()
    )
    today_close = (
        ShihyoMarketBiasSnapshot.objects
        .filter(date=today_local, mode=ShihyoMarketBiasSnapshot.MODE_CLOSE)
        .order_by("-updated_at")
        .first()
    )
    latest_close = (
        ShihyoMarketBiasSnapshot.objects
        .filter(mode=ShihyoMarketBiasSnapshot.MODE_CLOSE)
        .order_by("-date", "-updated_at")
        .first()
    )

    if current_time < dt_time(10, 0):
        for obj in [today_preopen, today_open_1000, today_close, latest_close]:
            if obj:
                return obj
        return None

    if current_time < dt_time(15, 30):
        for obj in [today_open_1000, today_preopen, today_close, latest_close]:
            if obj:
                return obj
        return None

    for obj in [today_close, today_open_1000, today_preopen, latest_close]:
        if obj:
            return obj
    return None


def _badge_class_from_tone(tone: str) -> str:
    if tone == ShihyoMarketBiasSnapshot.TONE_RISK_ON or tone == "risk_on":
        return "market-bias-badge market-bias-badge-on"
    if tone == ShihyoMarketBiasSnapshot.TONE_RISK_OFF or tone == "risk_off":
        return "market-bias-badge market-bias-badge-off"
    return "market-bias-badge market-bias-badge-wait"


def _mode_meta_from_mode(mode: str | None) -> tuple[str, str]:
    if mode == ShihyoMarketBiasSnapshot.MODE_OPEN_1000:
        return "10:00実績", "market-bias-mode market-bias-mode-open"
    if mode == ShihyoMarketBiasSnapshot.MODE_PREOPEN:
        return "朝予報", "market-bias-mode market-bias-mode-preopen"
    if mode == ShihyoMarketBiasSnapshot.MODE_CLOSE:
        return "引け後実績", "market-bias-mode market-bias-mode-close"
    return "保存値", "market-bias-mode market-bias-mode-raw"


def _build_market_bias_beginner_text(
    mode_label: str,
    strong_sectors: list[str],
    weak_sectors: list[str],
    hot_themes: list[str],
    cold_themes: list[str],
) -> str:
    prefix_map = {
        "10:00実績": "10:00時点では",
        "朝予報": "朝の予想では",
        "引け後実績": "引け後時点では",
        "保存値": "いまの保存データでは",
    }
    prefix = prefix_map.get(mode_label, "いまは")

    parts: list[str] = []

    if strong_sectors and weak_sectors:
        parts.append(f"{prefix}、{strong_sectors[0]}などに買いが入りやすく、{weak_sectors[0]}などは売られやすい流れです。")
    elif strong_sectors:
        parts.append(f"{prefix}、{strong_sectors[0]}などに買いが入りやすい流れです。")
    elif weak_sectors:
        parts.append(f"{prefix}、{weak_sectors[0]}などは売られやすい流れです。")
    else:
        parts.append(f"{prefix}、まだ大きな偏りは読み取り中です。")

    if hot_themes:
        parts.append(f"値上がり上位は{hot_themes[0]}寄りです。")
    if cold_themes:
        parts.append(f"値下がり上位は{cold_themes[0]}寄りです。")

    return " ".join(parts)


def _build_market_bias_compare(latest_bias: ShihyoMarketBiasSnapshot | None) -> dict:
    if latest_bias is None or latest_bias.mode != ShihyoMarketBiasSnapshot.MODE_OPEN_1000:
        return {
            "label": "",
            "class": "",
            "text": "",
        }

    preopen = (
        ShihyoMarketBiasSnapshot.objects
        .filter(date=latest_bias.date, mode=ShihyoMarketBiasSnapshot.MODE_PREOPEN)
        .order_by("-updated_at")
        .first()
    )

    if not preopen:
        return {
            "label": "",
            "class": "",
            "text": "",
        }

    open_strong = set(str(x) for x in (latest_bias.strong_sectors or []))
    open_weak = set(str(x) for x in (latest_bias.weak_sectors or []))
    pre_strong = set(str(x) for x in (preopen.strong_sectors or []))
    pre_weak = set(str(x) for x in (preopen.weak_sectors or []))

    same_side_overlap = len(open_strong & pre_strong) + len(open_weak & pre_weak)
    cross_overlap = len(open_strong & pre_weak) + len(open_weak & pre_strong)

    if same_side_overlap >= 2 and cross_overlap == 0:
        return {
            "label": "ほぼ一致",
            "class": "market-bias-compare-badge market-bias-compare-good",
            "text": "朝予報どおりの流れです。寄り後も主役業種が大きく変わっていません。",
        }

    if same_side_overlap >= 1 and cross_overlap <= 1:
        return {
            "label": "一部一致",
            "class": "market-bias-compare-badge market-bias-compare-mid",
            "text": "朝予報の一部は当たりですが、寄り後に少し入れ替わりも出ています。",
        }

    return {
        "label": "ズレあり",
        "class": "market-bias-compare-badge market-bias-compare-alert",
        "text": "朝予報と少し違う動きです。寄り後に資金の向きが変わっています。",
    }


def _build_market_bias_context(latest: MarketIndicatorSnapshot) -> dict:
    latest_bias = _select_market_bias_snapshot()

    if latest_bias:
        mode_label, mode_class = _mode_meta_from_mode(latest_bias.mode)
        strong_sectors = [str(x) for x in (latest_bias.strong_sectors or [])[:3]]
        weak_sectors = [str(x) for x in (latest_bias.weak_sectors or [])[:3]]
        hot_themes = [str(x) for x in (latest_bias.hot_themes or [])[:3]]
        cold_themes = [str(x) for x in (latest_bias.cold_themes or [])[:3]]
        compare = _build_market_bias_compare(latest_bias)

        return {
            "available": True,
            "summary_title": latest_bias.summary_title or "市場の偏り",
            "summary_badge_class": _badge_class_from_tone(latest_bias.tone),
            "summary_text": latest_bias.summary_text or "今日は市場の偏りが出ています。",
            "strong_sectors": strong_sectors,
            "weak_sectors": weak_sectors,
            "hot_themes": hot_themes,
            "cold_themes": cold_themes,
            "mode_label": mode_label,
            "mode_class": mode_class,
            "beginner_text": _build_market_bias_beginner_text(
                mode_label=mode_label,
                strong_sectors=strong_sectors,
                weak_sectors=weak_sectors,
                hot_themes=hot_themes,
                cold_themes=cold_themes,
            ),
            "compare_label": compare["label"],
            "compare_class": compare["class"],
            "compare_text": compare["text"],
        }

    raw_payload = latest.raw_payload if isinstance(latest.raw_payload, dict) else {}
    market_bias = raw_payload.get("market_bias") or {}

    strong_sectors = [str(x) for x in (market_bias.get("strong_sectors") or [])[:3]]
    weak_sectors = [str(x) for x in (market_bias.get("weak_sectors") or [])[:3]]
    hot_themes = [str(x) for x in (market_bias.get("hot_themes") or [])[:3]]
    cold_themes = [str(x) for x in (market_bias.get("cold_themes") or [])[:3]]

    if any([strong_sectors, weak_sectors, hot_themes, cold_themes]):
        mode_label, mode_class = _mode_meta_from_mode(None)
        summary_title = str(market_bias.get("summary_title") or "市場の偏り")
        summary_text = str(market_bias.get("summary_text") or "今日は市場の偏りが出ています。")
        tone = str(market_bias.get("tone") or "neutral")

        return {
            "available": True,
            "summary_title": summary_title,
            "summary_badge_class": _badge_class_from_tone(tone),
            "summary_text": summary_text,
            "strong_sectors": strong_sectors,
            "weak_sectors": weak_sectors,
            "hot_themes": hot_themes,
            "cold_themes": cold_themes,
            "mode_label": mode_label,
            "mode_class": mode_class,
            "beginner_text": _build_market_bias_beginner_text(
                mode_label=mode_label,
                strong_sectors=strong_sectors,
                weak_sectors=weak_sectors,
                hot_themes=hot_themes,
                cold_themes=cold_themes,
            ),
            "compare_label": "",
            "compare_class": "",
            "compare_text": "",
        }

    return {
        "available": False,
        "summary_title": "準備中",
        "summary_badge_class": "market-bias-badge market-bias-badge-wait",
        "summary_text": "日本株市場で、どこに資金が入っているか / どこが売られているか を表示します。",
        "strong_sectors": [],
        "weak_sectors": [],
        "hot_themes": [],
        "cold_themes": [],
        "mode_label": "準備中",
        "mode_class": "market-bias-mode market-bias-mode-raw",
        "beginner_text": "データがそろうと、ここに『今どこにお金が向かっているか』をやさしく表示します。",
        "compare_label": "",
        "compare_class": "",
        "compare_text": "",
    }


@login_required
def dashboard(request):
    latest = MarketIndicatorSnapshot.objects.first()

    context = {
        "latest": latest,
        "risk": None,
        "futures_card": None,
        "nikkei_spot_card": None,
        "fx_card": None,
        "vix_card": None,
        "ai_pred": None,
        "market_bias": None,
    }

    if latest:
        risk = _build_risk_context(latest)
        futures_card = _build_futures_card_context(latest)
        nikkei_spot_card = _build_nikkei_spot_card_context(latest)
        fx_card = _build_fx_card_context(latest)
        vix_card = _build_vix_card_context(latest)
        ai_pred = _build_ai_prediction_context(latest, risk)
        market_bias = _build_market_bias_context(latest)

        context["risk"] = risk
        context["futures_card"] = futures_card
        context["nikkei_spot_card"] = nikkei_spot_card
        context["fx_card"] = fx_card
        context["vix_card"] = vix_card
        context["ai_pred"] = ai_pred
        context["market_bias"] = market_bias

    return render(request, "shihyo/dashboard.html", context)