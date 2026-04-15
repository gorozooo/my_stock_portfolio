# =========================================================
# [FILE] monitor_service.py
# [PATH] <project_root>/tradeai/services/holdings/monitor_service.py
#
# このファイルは何？
# - 保有銘柄の監視ロジックをまとめたサービスです。
# - Holding の現在値（avg_cost / last_price / side / opened_at）に加えて、
#   日足から GC/DC と ATR を計算して、
#   継続・利確警戒・損切り警戒などを判定します。
# =========================================================

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from portfolio.models import Holding
from tradeai.services.indicators.atr_service import analyze_atr
from tradeai.services.indicators.daily_price_service import get_daily_ohlc
from tradeai.services.indicators.ma_cross_service import analyze_ma_cross


BROKER_LABELS = {
    "RAKUTEN": "楽天証券",
    "SBI": "SBI証券",
    "MATSUI": "松井証券",
}

ACCOUNT_LABELS = {
    "SPEC": "特定",
    "NISA": "NISA",
    "MARGIN": "信用",
}

LEVEL_RANK = {
    "STRONG": 0,
    "ATTENTION": 1,
    "REFERENCE": 2,
}

ACTION_RANK = {
    "STOP_LOSS_WARNING": 0,
    "SQUEEZE_WARNING": 0,
    "BUYBACK_CANDIDATE": 1,
    "TAKE_PROFIT_CANDIDATE": 1,
    "REVERSAL_WARNING": 2,
    "TAKE_PROFIT_CAUTION": 3,
    "MARGIN_STALE": 4,
    "CONTINUE": 5,
    "PRICE_MISSING": 6,
}


def _to_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _broker_label(code: str) -> str:
    return BROKER_LABELS.get(code or "", code or "-")


def _account_label(code: str) -> str:
    return ACCOUNT_LABELS.get(code or "", code or "-")


def _format_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def _calc_hold_days(holding: Holding) -> int | None:
    if not getattr(holding, "opened_at", None):
        return None
    try:
        return max(0, (holding.updated_at.date() - holding.opened_at).days)
    except Exception:
        return None


def _calc_pnl(holding: Holding) -> tuple[float | None, float | None]:
    avg_cost = _to_decimal(holding.avg_cost)
    last_price = _to_decimal(holding.last_price)
    qty = int(getattr(holding, "quantity", 0) or 0)

    if avg_cost is None or avg_cost <= 0 or last_price is None:
        return None, None

    if (holding.side or "").upper() == "SELL":
        pnl_pct = ((avg_cost - last_price) / avg_cost) * Decimal("100")
        pnl_yen = (avg_cost - last_price) * Decimal(qty)
    else:
        pnl_pct = ((last_price - avg_cost) / avg_cost) * Decimal("100")
        pnl_yen = (last_price - avg_cost) * Decimal(qty)

    return float(pnl_pct), float(pnl_yen)


def _load_technical_snapshot(holding: Holding) -> dict[str, Any]:
    ohlc = get_daily_ohlc(holding.ticker)
    if not ohlc:
        return {
            "has_technical": False,
            "ma_state": "UNKNOWN",
            "ma_label": "不明",
            "short_ma": None,
            "long_ma": None,
            "atr": None,
            "atr_pct": None,
        }

    ma_info = analyze_ma_cross(ohlc["closes"], short_window=5, long_window=25)
    atr_info = analyze_atr(ohlc["highs"], ohlc["lows"], ohlc["closes"], period=14)

    return {
        "has_technical": True,
        "ma_state": ma_info["ma_state"],
        "ma_label": ma_info["ma_label"],
        "short_ma": ma_info["short_ma"],
        "long_ma": ma_info["long_ma"],
        "atr": atr_info["atr"],
        "atr_pct": atr_info["atr_pct"],
    }


def _technical_summary(tech: dict[str, Any]) -> str:
    parts: list[str] = []

    if tech.get("ma_label") and tech.get("ma_label") != "不明":
        parts.append(f"5/25日線は {tech['ma_label']}")

    atr_pct = tech.get("atr_pct")
    if atr_pct is not None:
        parts.append(f"ATR(14)は {atr_pct:.2f}%")

    return "、".join(parts)


def _judge_long(
    pnl_pct: float | None,
    hold_days: int | None,
    account: str,
    tech: dict[str, Any],
) -> dict[str, Any]:
    ma_state = tech.get("ma_state")
    long_supportive = ma_state in ("GOLDEN_CROSS", "ABOVE_GC")
    long_adverse = ma_state in ("DEAD_CROSS", "BELOW_DC")
    tech_text = _technical_summary(tech)

    if pnl_pct is None:
        reason = "価格が未更新のため、まだ判定できません。"
        if tech_text:
            reason += f" ただし、{tech_text} です。"
        return {
            "action_key": "PRICE_MISSING",
            "action_label": "価格未更新",
            "level": "REFERENCE",
            "reason_text": reason,
            "emit_event": False,
            "score_total": 10,
        }

    if pnl_pct <= -8:
        reason = f"ロング保有の含み損が {_format_pct(pnl_pct)} です。損失拡大を防ぐため、見直し優先です。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "STOP_LOSS_WARNING",
            "action_label": "損切り警戒",
            "level": "STRONG",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 92,
        }

    if long_adverse and pnl_pct <= -2:
        reason = f"ロング保有は {_format_pct(pnl_pct)} で、5日線と25日線の関係も弱めです。反転警戒を優先したい状態です。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "REVERSAL_WARNING",
            "action_label": "反転警戒",
            "level": "ATTENTION",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 78,
        }

    if pnl_pct <= -4:
        reason = f"ロング保有の含み損が {_format_pct(pnl_pct)} です。逆行が続くなら損切り候補です。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "STOP_LOSS_WARNING",
            "action_label": "損切り警戒",
            "level": "ATTENTION",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 74,
        }

    if pnl_pct >= 15:
        reason = f"ロング保有の含み益が {_format_pct(pnl_pct)} まで伸びています。一部利確や逆指値引き上げ候補です。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "TAKE_PROFIT_CANDIDATE",
            "action_label": "利確候補",
            "level": "STRONG",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 90,
        }

    if pnl_pct >= 8:
        reason = f"ロング保有の含み益が {_format_pct(pnl_pct)} です。利確の準備も意識したい水準です。"
        if long_adverse:
            reason += " 5日線・25日線の形も少し弱いです。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "TAKE_PROFIT_CAUTION",
            "action_label": "利確警戒",
            "level": "ATTENTION",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 70,
        }

    if account == "MARGIN" and hold_days is not None and hold_days >= 30:
        reason = f"ロングの信用建玉が {hold_days} 日経過しています。長期化リスクの確認をおすすめします。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "MARGIN_STALE",
            "action_label": "信用長期化注意",
            "level": "ATTENTION",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 62,
        }

    reason = f"ロング継続です。現在損益は {_format_pct(pnl_pct)} です。"
    if long_supportive:
        reason += " 5日線と25日線の関係は追い風寄りです。"
    elif long_adverse:
        reason += " ただし、5日線と25日線の関係は弱めです。"
    if tech_text:
        reason += f" {tech_text}。"

    return {
        "action_key": "CONTINUE",
        "action_label": "継続保有",
        "level": "REFERENCE",
        "reason_text": reason,
        "emit_event": False,
        "score_total": 25,
    }


def _judge_short(
    pnl_pct: float | None,
    hold_days: int | None,
    account: str,
    tech: dict[str, Any],
) -> dict[str, Any]:
    ma_state = tech.get("ma_state")
    short_supportive = ma_state in ("DEAD_CROSS", "BELOW_DC")
    short_adverse = ma_state in ("GOLDEN_CROSS", "ABOVE_GC")
    tech_text = _technical_summary(tech)

    if pnl_pct is None:
        reason = "価格が未更新のため、まだ判定できません。"
        if tech_text:
            reason += f" ただし、{tech_text} です。"
        return {
            "action_key": "PRICE_MISSING",
            "action_label": "価格未更新",
            "level": "REFERENCE",
            "reason_text": reason,
            "emit_event": False,
            "score_total": 10,
        }

    if pnl_pct <= -8:
        reason = f"ショート保有の損益が {_format_pct(pnl_pct)} です。逆行が大きく、踏み上げ警戒が強い状態です。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "SQUEEZE_WARNING",
            "action_label": "踏み上げ警戒",
            "level": "STRONG",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 93,
        }

    if short_adverse and pnl_pct <= -2:
        reason = f"ショート保有は {_format_pct(pnl_pct)} で、5日線と25日線の関係も逆風寄りです。踏み上げ警戒を優先したい状態です。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "REVERSAL_WARNING",
            "action_label": "反転警戒",
            "level": "ATTENTION",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 79,
        }

    if pnl_pct <= -4:
        reason = f"ショート保有の損益が {_format_pct(pnl_pct)} です。逆行が続くなら見直し候補です。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "STOP_LOSS_WARNING",
            "action_label": "損切り警戒",
            "level": "ATTENTION",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 75,
        }

    if pnl_pct >= 15:
        reason = f"ショート保有の含み益が {_format_pct(pnl_pct)} まで伸びています。買い戻し候補として強めです。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "BUYBACK_CANDIDATE",
            "action_label": "買い戻し候補",
            "level": "STRONG",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 91,
        }

    if pnl_pct >= 8:
        reason = f"ショート保有の含み益が {_format_pct(pnl_pct)} です。利確を意識したい水準です。"
        if short_adverse:
            reason += " 5日線・25日線の形は少し逆風です。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "TAKE_PROFIT_CANDIDATE",
            "action_label": "利確候補",
            "level": "ATTENTION",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 71,
        }

    if account == "MARGIN" and hold_days is not None and hold_days >= 30:
        reason = f"ショートの信用建玉が {hold_days} 日経過しています。長期化リスクの確認をおすすめします。"
        if tech_text:
            reason += f" {tech_text}。"
        return {
            "action_key": "MARGIN_STALE",
            "action_label": "信用長期化注意",
            "level": "ATTENTION",
            "reason_text": reason,
            "emit_event": True,
            "score_total": 62,
        }

    reason = f"ショート継続です。現在損益は {_format_pct(pnl_pct)} です。"
    if short_supportive:
        reason += " 5日線と25日線の関係は追い風寄りです。"
    elif short_adverse:
        reason += " ただし、5日線と25日線の関係は逆風寄りです。"
    if tech_text:
        reason += f" {tech_text}。"

    return {
        "action_key": "CONTINUE",
        "action_label": "継続保有",
        "level": "REFERENCE",
        "reason_text": reason,
        "emit_event": False,
        "score_total": 25,
    }


def build_holding_monitor_row(holding: Holding) -> dict[str, Any]:
    pnl_pct, pnl_yen = _calc_pnl(holding)
    hold_days = _calc_hold_days(holding)
    tech = _load_technical_snapshot(holding)

    direction = "SHORT" if (holding.side or "").upper() == "SELL" else "LONG"
    direction_label = "ショート" if direction == "SHORT" else "ロング"

    if direction == "SHORT":
        judged = _judge_short(pnl_pct, hold_days, holding.account, tech)
    else:
        judged = _judge_long(pnl_pct, hold_days, holding.account, tech)

    return {
        "holding": holding,
        "ticker": holding.ticker,
        "name": holding.name or "",
        "direction": direction,
        "direction_label": direction_label,
        "broker": holding.broker,
        "broker_label": _broker_label(holding.broker),
        "account": holding.account,
        "account_label": _account_label(holding.account),
        "quantity": int(holding.quantity or 0),
        "avg_cost": float(holding.avg_cost or 0),
        "last_price": float(holding.last_price) if holding.last_price is not None else None,
        "hold_days": hold_days,
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        "pnl_yen": round(pnl_yen, 0) if pnl_yen is not None else None,
        "has_price": pnl_pct is not None,
        "ma_state": tech["ma_state"],
        "ma_label": tech["ma_label"],
        "short_ma": tech["short_ma"],
        "long_ma": tech["long_ma"],
        "atr": tech["atr"],
        "atr_pct": tech["atr_pct"],
        "technical_summary": _technical_summary(tech),
        **judged,
    }


def build_holding_monitor_rows(user) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    holdings = (
        Holding.objects.filter(user=user, quantity__gt=0)
        .order_by("broker", "account", "-updated_at", "ticker")
    )

    for holding in holdings:
        rows.append(build_holding_monitor_row(holding))

    rows.sort(
        key=lambda row: (
            LEVEL_RANK.get(row["level"], 9),
            ACTION_RANK.get(row["action_key"], 9),
            -(abs(row["pnl_pct"]) if row["pnl_pct"] is not None else -1),
            row["ticker"],
        )
    )
    return rows