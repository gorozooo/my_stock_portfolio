# =========================================================
# [FILE] monitor_service.py
# [PATH] <project_root>/tradeai/services/holdings/monitor_service.py
#
# このファイルは何？
# - 保有銘柄の監視ロジックをまとめたサービスです。
# - Holding の現在値（avg_cost / last_price / side / opened_at）から
#   継続・利確警戒・損切り警戒などを判定します。
# =========================================================

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from portfolio.models import Holding


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
    "TAKE_PROFIT_CAUTION": 2,
    "MARGIN_STALE": 3,
    "CONTINUE": 4,
    "PRICE_MISSING": 5,
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


def _judge_long(pnl_pct: float | None, hold_days: int | None, account: str) -> dict[str, Any]:
    if pnl_pct is None:
        return {
            "action_key": "PRICE_MISSING",
            "action_label": "価格未更新",
            "level": "REFERENCE",
            "reason_text": "価格が未更新のため、まだ判定できません。",
            "emit_event": False,
            "score_total": 10,
        }

    if pnl_pct <= -8:
        return {
            "action_key": "STOP_LOSS_WARNING",
            "action_label": "損切り警戒",
            "level": "STRONG",
            "reason_text": f"ロング保有の含み損が {_format_pct(pnl_pct)} です。損失拡大を防ぐため、見直し優先です。",
            "emit_event": True,
            "score_total": 92,
        }

    if pnl_pct <= -4:
        return {
            "action_key": "STOP_LOSS_WARNING",
            "action_label": "損切り警戒",
            "level": "ATTENTION",
            "reason_text": f"ロング保有の含み損が {_format_pct(pnl_pct)} です。逆行が続くなら損切り候補です。",
            "emit_event": True,
            "score_total": 74,
        }

    if pnl_pct >= 15:
        return {
            "action_key": "TAKE_PROFIT_CANDIDATE",
            "action_label": "利確候補",
            "level": "STRONG",
            "reason_text": f"ロング保有の含み益が {_format_pct(pnl_pct)} まで伸びています。一部利確や逆指値引き上げ候補です。",
            "emit_event": True,
            "score_total": 90,
        }

    if pnl_pct >= 8:
        return {
            "action_key": "TAKE_PROFIT_CAUTION",
            "action_label": "利確警戒",
            "level": "ATTENTION",
            "reason_text": f"ロング保有の含み益が {_format_pct(pnl_pct)} です。伸びていますが、利確の準備も意識したい水準です。",
            "emit_event": True,
            "score_total": 70,
        }

    if account == "MARGIN" and hold_days is not None and hold_days >= 30:
        return {
            "action_key": "MARGIN_STALE",
            "action_label": "信用長期化注意",
            "level": "ATTENTION",
            "reason_text": f"ロングの信用建玉が {hold_days} 日経過しています。長期化リスクの確認をおすすめします。",
            "emit_event": True,
            "score_total": 62,
        }

    return {
        "action_key": "CONTINUE",
        "action_label": "継続保有",
        "level": "REFERENCE",
        "reason_text": f"ロング継続です。現在損益は {_format_pct(pnl_pct)} で、大きな利確/損切り基準にはまだ到達していません。",
        "emit_event": False,
        "score_total": 25,
    }


def _judge_short(pnl_pct: float | None, hold_days: int | None, account: str) -> dict[str, Any]:
    if pnl_pct is None:
        return {
            "action_key": "PRICE_MISSING",
            "action_label": "価格未更新",
            "level": "REFERENCE",
            "reason_text": "価格が未更新のため、まだ判定できません。",
            "emit_event": False,
            "score_total": 10,
        }

    if pnl_pct <= -8:
        return {
            "action_key": "SQUEEZE_WARNING",
            "action_label": "踏み上げ警戒",
            "level": "STRONG",
            "reason_text": f"ショート保有の損益が {_format_pct(pnl_pct)} です。逆行が大きく、踏み上げ警戒が強い状態です。",
            "emit_event": True,
            "score_total": 93,
        }

    if pnl_pct <= -4:
        return {
            "action_key": "STOP_LOSS_WARNING",
            "action_label": "損切り警戒",
            "level": "ATTENTION",
            "reason_text": f"ショート保有の損益が {_format_pct(pnl_pct)} です。逆行が続くなら見直し候補です。",
            "emit_event": True,
            "score_total": 75,
        }

    if pnl_pct >= 15:
        return {
            "action_key": "BUYBACK_CANDIDATE",
            "action_label": "買い戻し候補",
            "level": "STRONG",
            "reason_text": f"ショート保有の含み益が {_format_pct(pnl_pct)} まで伸びています。買い戻し候補として強めです。",
            "emit_event": True,
            "score_total": 91,
        }

    if pnl_pct >= 8:
        return {
            "action_key": "TAKE_PROFIT_CANDIDATE",
            "action_label": "利確候補",
            "level": "ATTENTION",
            "reason_text": f"ショート保有の含み益が {_format_pct(pnl_pct)} です。利確を意識したい水準です。",
            "emit_event": True,
            "score_total": 71,
        }

    if account == "MARGIN" and hold_days is not None and hold_days >= 30:
        return {
            "action_key": "MARGIN_STALE",
            "action_label": "信用長期化注意",
            "level": "ATTENTION",
            "reason_text": f"ショートの信用建玉が {hold_days} 日経過しています。長期化リスクの確認をおすすめします。",
            "emit_event": True,
            "score_total": 62,
        }

    return {
        "action_key": "CONTINUE",
        "action_label": "継続保有",
        "level": "REFERENCE",
        "reason_text": f"ショート継続です。現在損益は {_format_pct(pnl_pct)} で、大きな利確/損切り基準にはまだ到達していません。",
        "emit_event": False,
        "score_total": 25,
    }


def build_holding_monitor_row(holding: Holding) -> dict[str, Any]:
    pnl_pct, pnl_yen = _calc_pnl(holding)
    hold_days = _calc_hold_days(holding)

    direction = "SHORT" if (holding.side or "").upper() == "SELL" else "LONG"
    direction_label = "ショート" if direction == "SHORT" else "ロング"

    if direction == "SHORT":
        judged = _judge_short(pnl_pct, hold_days, holding.account)
    else:
        judged = _judge_long(pnl_pct, hold_days, holding.account)

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