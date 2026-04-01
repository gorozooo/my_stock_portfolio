# [FILE] summary_service.py
# [PATH] portfolio/services/holdings/summary_service.py
#
# このファイルは何？
# - /holdings/summary/ 専用の集計サービス
# - 保有一覧の RowVM 群から、分析ページに必要な集計をまとめて返す
#
# 今回の方針
# - 保有ページは「操作専用」
# - サマリーは「分析専用」
# - 全体状態 / 配分 / 損益 / 時間 / 配当 / リスク / コメント を1回で作る

# -*- coding: utf-8 -*-
from __future__ import annotations

from statistics import median
from typing import Any


def _to_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _safe_name(v: str | None, default: str = "—") -> str:
    s = (v or "").strip()
    return s or default


def _broker_label(h) -> str:
    try:
        return h.get_broker_display()
    except Exception:
        return _safe_name(getattr(h, "broker", None), "—")


def _account_label(h) -> str:
    code = (getattr(h, "account", "") or "").upper()
    return {
        "SPEC": "特定",
        "NISA": "NISA",
        "MARGIN": "信用",
        "OTHER": "その他",
    }.get(code, code or "—")


def _sector_label(h) -> str:
    return _safe_name(getattr(h, "sector", None), "未分類")


def _fx_open_for_holding(h) -> float:
    cur = (getattr(h, "currency", "") or "JPY").upper()
    if cur == "JPY":
        return 1.0

    fx = _to_float(getattr(h, "fx_rate", None), 0.0)
    if fx > 0:
        return fx
    return 1.0


def _acq_jpy(row) -> float:
    h = row.obj
    qty = int(getattr(h, "quantity", 0) or 0)
    avg_cost = _to_float(getattr(h, "avg_cost", None), 0.0)
    fx = _fx_open_for_holding(h)
    return qty * avg_cost * fx


def _days_of(row) -> int | None:
    try:
        if row.days is None:
            return None
        return int(row.days)
    except Exception:
        return None


def _pct(part: float, whole: float) -> float | None:
    if whole <= 0:
        return None
    return (part / whole) * 100.0


def _mix_rows(src: dict[str, dict[str, Any]], whole: float, sort_key: str = "value") -> list[dict[str, Any]]:
    rows = []
    for label, vals in src.items():
        value = _to_float(vals.get("value"), 0.0)
        pnl = _to_float(vals.get("pnl"), 0.0)
        count = int(vals.get("count") or 0)
        rows.append(
            {
                "label": label,
                "value": value,
                "pnl": pnl,
                "count": count,
                "pct": _pct(value, whole),
            }
        )
    rows.sort(key=lambda x: x.get(sort_key, 0.0), reverse=True)
    return rows


def _top_rows(rows: list[dict[str, Any]], key: str, reverse: bool = True, limit: int = 5) -> list[dict[str, Any]]:
    items = [r for r in rows]
    items.sort(key=lambda x: x.get(key, 0.0), reverse=reverse)
    return items[:limit]


def _build_position_rows(rows) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        h = r.obj
        acq = _acq_jpy(r)
        valuation = _to_float(getattr(r, "valuation", None), 0.0)
        pnl = _to_float(getattr(r, "pnl", None), 0.0)
        pnl_pct = getattr(r, "pnl_pct", None)
        pnl_pct = _to_float(pnl_pct, 0.0) if pnl_pct is not None else None
        days = _days_of(r)
        annual_div = _to_float(getattr(r, "div_annual", None), 0.0)
        received_div = _to_float(getattr(r, "div_received", None), 0.0)
        qty = int(getattr(h, "quantity", 0) or 0)
        yield_cost = _pct(annual_div, acq)

        out.append(
            {
                "ticker": _safe_name(getattr(h, "ticker", None)),
                "name": _safe_name(getattr(h, "name", None)),
                "broker": _broker_label(h),
                "account": _account_label(h),
                "sector": _sector_label(h),
                "side": _safe_name(getattr(h, "side", None)),
                "currency": _safe_name(getattr(h, "currency", None), "JPY"),
                "qty": qty,
                "acq": acq,
                "valuation": valuation,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "days": days,
                "annual_div": annual_div,
                "received_div": received_div,
                "yield_cost": yield_cost,
                "opened_at": getattr(h, "opened_at", None),
                "memo": getattr(h, "memo", "") or "",
            }
        )
    return out


def _risk_flags(
    margin_ratio: float | None,
    top_position_ratio: float | None,
    top5_ratio: float | None,
    sector_top_pct: float | None,
    stuck_count: int,
    negative_count: int,
) -> list[str]:
    flags: list[str] = []

    if margin_ratio is not None and margin_ratio >= 35:
        flags.append(f"信用比率が {margin_ratio:.1f}% と高めです。")
    if top_position_ratio is not None and top_position_ratio >= 25:
        flags.append(f"1銘柄集中が {top_position_ratio:.1f}% あります。")
    if top5_ratio is not None and top5_ratio >= 60:
        flags.append(f"上位5銘柄で {top5_ratio:.1f}% を占めています。")
    if sector_top_pct is not None and sector_top_pct >= 40:
        flags.append(f"最大セクター比率が {sector_top_pct:.1f}% あります。")
    if stuck_count >= 2:
        flags.append(f"含み損の長期化銘柄が {stuck_count} 件あります。")
    if negative_count >= 3:
        flags.append(f"含み損銘柄が {negative_count} 件あります。")

    if not flags:
        flags.append("大きな偏りは今のところ目立っていません。")

    return flags


def _comment_lines(
    count: int,
    total_val: float,
    total_pnl: float,
    total_pnl_pct: float | None,
    biggest_broker: dict[str, Any] | None,
    biggest_account: dict[str, Any] | None,
    biggest_sector: dict[str, Any] | None,
    top_position_ratio: float | None,
    annual_div_total: float,
    risk_score: int,
) -> list[str]:
    lines: list[str] = []

    pnl_text = f"{total_pnl:,.0f}円"
    if total_pnl_pct is not None:
        lines.append(
            f"現在の保有は {count} 件、評価額合計は {total_val:,.0f} 円、含み損益は {pnl_text}（{total_pnl_pct:.2f}%）です。"
        )
    else:
        lines.append(
            f"現在の保有は {count} 件、評価額合計は {total_val:,.0f} 円、含み損益は {pnl_text} です。"
        )

    if biggest_broker:
        lines.append(
            f"証券会社では {biggest_broker['label']} の比重が最も大きく、全体の {biggest_broker.get('pct') or 0:.1f}% を占めています。"
        )

    if biggest_account:
        lines.append(
            f"口座区分では {biggest_account['label']} が中心で、全体の {biggest_account.get('pct') or 0:.1f}% です。"
        )

    if biggest_sector:
        lines.append(
            f"セクターは {biggest_sector['label']} への偏りが最も強く、構成比は {biggest_sector.get('pct') or 0:.1f}% です。"
        )

    if top_position_ratio is not None:
        lines.append(
            f"最大保有1銘柄の比率は {top_position_ratio:.1f}% です。"
        )

    lines.append(
        f"年間配当見込みは {annual_div_total:,.0f} 円、保有全体の注意スコアは {risk_score} / 100 です。"
    )

    return lines


def empty_summary() -> dict[str, Any]:
    return {
        "count": 0,
        "total_acq": 0.0,
        "total_val": 0.0,
        "total_pnl": 0.0,
        "total_pnl_pct": None,
        "winners": 0,
        "losers": 0,
        "flat": 0,
        "win_ratio": None,
        "avg_days": None,
        "median_days": None,
        "annual_div_total": 0.0,
        "received_div_total": 0.0,
        "margin_ratio": None,
        "nisa_ratio": None,
        "top_position_ratio": None,
        "top5_ratio": None,
        "risk_score": 0,
        "risk_flags": [],
        "broker_mix": [],
        "account_mix": [],
        "sector_mix": [],
        "broker_pnl": [],
        "account_pnl": [],
        "sector_pnl": [],
        "top_positions": [],
        "top_gainers": [],
        "top_losers": [],
        "long_holdings": [],
        "margin_long_holdings": [],
        "stuck_holdings": [],
        "quick_winners": [],
        "dividend_rank": [],
        "yield_rank": [],
        "broker_dividend": [],
        "comment_lines": ["この条件の保有はまだありません。"],
    }


def build_summary(rows) -> dict[str, Any]:
    if not rows:
        return empty_summary()

    positions = _build_position_rows(rows)

    total_acq = sum(x["acq"] for x in positions)
    total_val = sum(x["valuation"] for x in positions)
    total_pnl = sum(x["pnl"] for x in positions)
    total_pnl_pct = _pct(total_pnl, total_acq)

    winners = sum(1 for x in positions if x["pnl"] > 0)
    losers = sum(1 for x in positions if x["pnl"] < 0)
    flat = sum(1 for x in positions if x["pnl"] == 0)
    win_ratio = _pct(winners, winners + losers) if (winners + losers) > 0 else None

    days_list = [x["days"] for x in positions if x["days"] is not None]
    avg_days = (sum(days_list) / len(days_list)) if days_list else None
    median_days = median(days_list) if days_list else None

    annual_div_total = sum(x["annual_div"] for x in positions)
    received_div_total = sum(x["received_div"] for x in positions)

    broker_totals: dict[str, dict[str, Any]] = {}
    account_totals: dict[str, dict[str, Any]] = {}
    sector_totals: dict[str, dict[str, Any]] = {}
    broker_dividend_totals: dict[str, dict[str, Any]] = {}

    for x in positions:
        for bag, label in (
            (broker_totals, x["broker"]),
            (account_totals, x["account"]),
            (sector_totals, x["sector"]),
        ):
            if label not in bag:
                bag[label] = {"value": 0.0, "pnl": 0.0, "count": 0}
            bag[label]["value"] += x["valuation"]
            bag[label]["pnl"] += x["pnl"]
            bag[label]["count"] += 1

        if x["broker"] not in broker_dividend_totals:
            broker_dividend_totals[x["broker"]] = {"value": 0.0, "pnl": 0.0, "count": 0}
        broker_dividend_totals[x["broker"]]["value"] += x["annual_div"]
        broker_dividend_totals[x["broker"]]["count"] += 1

    broker_mix = _mix_rows(broker_totals, total_val)
    account_mix = _mix_rows(account_totals, total_val)
    sector_mix = _mix_rows(sector_totals, total_val)

    broker_pnl = sorted(
        [{"label": k, "pnl": v["pnl"], "count": v["count"]} for k, v in broker_totals.items()],
        key=lambda x: x["pnl"],
        reverse=True,
    )
    account_pnl = sorted(
        [{"label": k, "pnl": v["pnl"], "count": v["count"]} for k, v in account_totals.items()],
        key=lambda x: x["pnl"],
        reverse=True,
    )
    sector_pnl = sorted(
        [{"label": k, "pnl": v["pnl"], "count": v["count"]} for k, v in sector_totals.items()],
        key=lambda x: x["pnl"],
        reverse=True,
    )

    top_positions = _top_rows(positions, key="valuation", reverse=True, limit=5)
    top_gainers = _top_rows([x for x in positions if x["pnl"] > 0], key="pnl", reverse=True, limit=5)
    top_losers = _top_rows([x for x in positions if x["pnl"] < 0], key="pnl", reverse=False, limit=5)

    long_holdings = _top_rows([x for x in positions if x["days"] is not None], key="days", reverse=True, limit=5)
    margin_long_holdings = _top_rows(
        [x for x in positions if x["days"] is not None and x["account"] == "信用"],
        key="days",
        reverse=True,
        limit=5,
    )
    stuck_holdings = _top_rows(
        [x for x in positions if x["days"] is not None and x["days"] >= 60 and x["pnl"] < 0],
        key="days",
        reverse=True,
        limit=5,
    )
    quick_winners = _top_rows(
        [x for x in positions if x["days"] is not None and x["days"] <= 30 and x["pnl"] > 0],
        key="pnl",
        reverse=True,
        limit=5,
    )

    dividend_rank = _top_rows([x for x in positions if x["annual_div"] > 0], key="annual_div", reverse=True, limit=5)
    yield_rank = _top_rows(
        [x for x in positions if x["yield_cost"] is not None and x["yield_cost"] > 0],
        key="yield_cost",
        reverse=True,
        limit=5,
    )

    broker_dividend = sorted(
        [{"label": k, "value": v["value"], "count": v["count"]} for k, v in broker_dividend_totals.items()],
        key=lambda x: x["value"],
        reverse=True,
    )

    account_value_map = {x["label"]: x["value"] for x in account_mix}
    margin_ratio = _pct(account_value_map.get("信用", 0.0), total_val)
    nisa_ratio = _pct(account_value_map.get("NISA", 0.0), total_val)

    top_position_ratio = _pct(top_positions[0]["valuation"], total_val) if top_positions else None
    top5_ratio = _pct(sum(x["valuation"] for x in top_positions), total_val) if top_positions else None
    sector_top_pct = sector_mix[0]["pct"] if sector_mix else None

    risk_score = 0
    if margin_ratio is not None:
        risk_score += min(30, int(round(margin_ratio)))
    if top_position_ratio is not None:
        risk_score += min(25, int(round(top_position_ratio)))
    if top5_ratio is not None:
        risk_score += min(20, int(round(max(0.0, top5_ratio - 40.0) / 2.0)))
    if sector_top_pct is not None:
        risk_score += min(15, int(round(max(0.0, sector_top_pct - 25.0) / 2.0)))
    risk_score += min(10, len(stuck_holdings) * 3)
    risk_score = min(100, risk_score)

    risk_flags = _risk_flags(
        margin_ratio=margin_ratio,
        top_position_ratio=top_position_ratio,
        top5_ratio=top5_ratio,
        sector_top_pct=sector_top_pct,
        stuck_count=len(stuck_holdings),
        negative_count=losers,
    )

    biggest_broker = broker_mix[0] if broker_mix else None
    biggest_account = account_mix[0] if account_mix else None
    biggest_sector = sector_mix[0] if sector_mix else None

    comment_lines = _comment_lines(
        count=len(positions),
        total_val=total_val,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
        biggest_broker=biggest_broker,
        biggest_account=biggest_account,
        biggest_sector=biggest_sector,
        top_position_ratio=top_position_ratio,
        annual_div_total=annual_div_total,
        risk_score=risk_score,
    )

    return {
        "count": len(positions),
        "total_acq": total_acq,
        "total_val": total_val,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "winners": winners,
        "losers": losers,
        "flat": flat,
        "win_ratio": win_ratio,
        "avg_days": avg_days,
        "median_days": median_days,
        "annual_div_total": annual_div_total,
        "received_div_total": received_div_total,
        "margin_ratio": margin_ratio,
        "nisa_ratio": nisa_ratio,
        "top_position_ratio": top_position_ratio,
        "top5_ratio": top5_ratio,
        "risk_score": risk_score,
        "risk_flags": risk_flags,
        "broker_mix": broker_mix,
        "account_mix": account_mix,
        "sector_mix": sector_mix,
        "broker_pnl": broker_pnl,
        "account_pnl": account_pnl,
        "sector_pnl": sector_pnl,
        "top_positions": top_positions,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "long_holdings": long_holdings,
        "margin_long_holdings": margin_long_holdings,
        "stuck_holdings": stuck_holdings,
        "quick_winners": quick_winners,
        "dividend_rank": dividend_rank,
        "yield_rank": yield_rank,
        "broker_dividend": broker_dividend,
        "comment_lines": comment_lines,
    }