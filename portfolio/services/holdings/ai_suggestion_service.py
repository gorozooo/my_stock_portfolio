# [FILE] ai_suggestion_service.py
# [PATH] portfolio/services/holdings/ai_suggestion_service.py
#
# このファイルは何？
# - /holdings/ai/ 専用の提案サービス
# - 保有RowVMから、AI提案ページ用の候補を作る
#
# 今回の方針
# - まずはルールベースで実務的な提案を出す
# - 総合 / 利確 / 損切り警戒 / 継続 / 現引 / NISA / 慎重 を作る
# - 証券会社 × 対象(全体/現物/信用) ごとに切り替えられる形にする

# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Optional


BROKER_ORDER = [
    ("ALL", "全体"),
    ("RAKUTEN", "楽天"),
    ("MATSUI", "松井"),
    ("SBI", "SBI"),
]

SCOPE_ORDER = [
    ("all", "全体"),
    ("spot", "現物"),
    ("margin", "信用"),
]

CATEGORY_ORDER = [
    ("overall", "総合"),
    ("take_profit", "利確"),
    ("stop_alert", "損切り警戒"),
    ("continue", "継続"),
    ("margin_to_spot", "現引"),
    ("nisa", "NISA"),
    ("cautious", "慎重"),
]


def _to_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _safe_text(v, default: str = "—") -> str:
    s = str(v or "").strip()
    return s or default


def _broker_label(h) -> str:
    code = (getattr(h, "broker", "") or "").upper()
    return {
        "RAKUTEN": "楽天",
        "MATSUI": "松井",
        "SBI": "SBI",
    }.get(code, code or "—")


def _account_label(h) -> str:
    code = (getattr(h, "account", "") or "").upper()
    return {
        "SPEC": "特定",
        "NISA": "NISA",
        "MARGIN": "信用",
        "OTHER": "その他",
    }.get(code, code or "—")


def _account_key(h) -> str:
    return (getattr(h, "account", "") or "").upper()


def _currency_symbol(h) -> str:
    cur = (getattr(h, "currency", "") or "JPY").upper()
    return "$" if cur == "USD" else "¥"


def _days_of(row) -> Optional[int]:
    try:
        if row.days is None:
            return None
        return int(row.days)
    except Exception:
        return None


def _acq_jpy(row) -> float:
    h = row.obj
    qty = int(getattr(h, "quantity", 0) or 0)
    avg_cost = _to_float(getattr(h, "avg_cost", None), 0.0)
    fx = _to_float(getattr(h, "fx_rate", None), 1.0)
    cur = (getattr(h, "currency", "") or "JPY").upper()
    if cur == "JPY":
        fx = 1.0
    if fx <= 0:
        fx = 1.0
    return qty * avg_cost * fx


def _pct(part: float, whole: float) -> Optional[float]:
    if whole <= 0:
        return None
    return (part / whole) * 100.0


def normalize_broker(raw: Optional[str]) -> str:
    value = (raw or "").strip().upper()
    valid = {code for code, _label in BROKER_ORDER}
    if value in valid:
        return value
    return "ALL"


def normalize_scope(raw: Optional[str]) -> str:
    value = (raw or "").strip().lower()
    valid = {code for code, _label in SCOPE_ORDER}
    if value in valid:
        return value
    return "all"


def normalize_category(raw: Optional[str]) -> str:
    value = (raw or "").strip().lower()
    valid = {code for code, _label in CATEGORY_ORDER}
    if value in valid:
        return value
    return "overall"


def _scope_match(h, scope_key: str) -> bool:
    account = _account_key(h)
    if scope_key == "all":
        return True
    if scope_key == "spot":
        return account in ("SPEC", "NISA")
    if scope_key == "margin":
        return account == "MARGIN"
    return True


def _broker_match(h, broker_key: str) -> bool:
    if broker_key == "ALL":
        return True
    return (getattr(h, "broker", "") or "").upper() == broker_key


def _make_item(row, total_val: float, score: Optional[int] = None, reasons: Optional[list[str]] = None, suggestion: str = "") -> dict[str, Any]:
    h = row.obj
    valuation = _to_float(getattr(row, "valuation", None), 0.0)
    pnl = _to_float(getattr(row, "pnl", None), 0.0)

    pnl_pct_raw = getattr(row, "pnl_pct", None)
    pnl_pct = _to_float(pnl_pct_raw, 0.0) if pnl_pct_raw is not None else None

    days = _days_of(row)
    annual_div = _to_float(getattr(row, "div_annual", None), 0.0)
    qty = int(getattr(h, "quantity", 0) or 0)
    acq = _acq_jpy(row)
    yield_cost = _pct(annual_div, acq)
    ratio = _pct(valuation, total_val)
    can_margin_to_spot = (
        _account_key(h) == "MARGIN"
        and _safe_text(getattr(h, "side", None), "").upper() == "BUY"
    )

    reason_list = reasons or []
    reason_text = " / ".join(reason_list) if reason_list else ""

    badges: list[dict[str, str]] = []
    if suggestion:
        badges.append({"kind": "suggestion", "label": suggestion})
    if score is not None:
        badges.append({"kind": "score", "label": f"優先 {score}"})
    if ratio is not None and ratio > 0:
        badges.append({"kind": "ratio", "label": f"構成 {ratio:.1f}%"})

    return {
        "holding_id": getattr(h, "id", None),
        "ticker": _safe_text(getattr(h, "ticker", None)),
        "name": _safe_text(getattr(h, "name", None)),
        "broker": _broker_label(h),
        "account": _account_label(h),
        "account_key": _account_key(h),
        "currency_symbol": _currency_symbol(h),
        "qty": qty,
        "valuation": valuation,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "days": days,
        "annual_div": annual_div,
        "yield_cost": yield_cost,
        "ratio": ratio,
        "score": score,
        "reason_text": reason_text,
        "reasons": reason_list,
        "suggestion": suggestion,
        "badges": badges[:3],
        "can_margin_to_spot": can_margin_to_spot,
    }


def _build_groups(rows) -> dict[str, Any]:
    total_val = sum(_to_float(getattr(r, "valuation", None), 0.0) for r in rows)

    base_rows = []
    for r in rows:
        base_rows.append(
            _make_item(r, total_val=total_val)
        )

    take_profit = []
    stop_alert = []
    continue_items = []
    margin_to_spot = []
    nisa_items = []
    cautious = []

    for item in base_rows:
        score = 0
        reasons: list[str] = []

        pnl_pct = item["pnl_pct"]
        days = item["days"]
        ratio = item["ratio"]
        account_key = item["account_key"]

        # 利確
        if item["pnl"] > 0 and (
            (pnl_pct is not None and pnl_pct >= 8.0)
            or (days is not None and days >= 30)
            or (ratio is not None and ratio >= 18.0)
        ):
            score = 50
            if pnl_pct is not None:
                score += min(20, int(pnl_pct))
            if ratio is not None:
                score += min(15, int(ratio))
            reasons = []
            if pnl_pct is not None and pnl_pct >= 8.0:
                reasons.append(f"損益率が {pnl_pct:.2f}%")
            if days is not None and days >= 30:
                reasons.append(f"保有 {days}日")
            if ratio is not None and ratio >= 18.0:
                reasons.append(f"構成比 {ratio:.1f}%")
            take_profit.append(_make_item_obj(item, score, reasons, "利確候補"))

        # 損切り警戒
        if item["pnl"] < 0 and (
            (pnl_pct is not None and pnl_pct <= -7.0)
            or account_key == "MARGIN"
            or (days is not None and days >= 30)
        ):
            score = 55
            if pnl_pct is not None:
                score += min(25, int(abs(pnl_pct)))
            if account_key == "MARGIN":
                score += 10
            if days is not None:
                score += min(10, int(days / 30))
            reasons = []
            if pnl_pct is not None and pnl_pct <= -7.0:
                reasons.append(f"損益率が {pnl_pct:.2f}%")
            if account_key == "MARGIN":
                reasons.append("信用保有")
            if days is not None and days >= 30:
                reasons.append(f"保有 {days}日")
            stop_alert.append(_make_item_obj(item, score, reasons, "見直し候補"))

        # 継続
        if item["pnl"] >= 0 and (
            (pnl_pct is not None and 0.0 <= pnl_pct <= 10.0)
            or item["annual_div"] > 0
        ):
            score = 40
            if pnl_pct is not None:
                score += min(10, int(max(0.0, pnl_pct)))
            if item["annual_div"] > 0:
                score += 8
            reasons = []
            if pnl_pct is not None and 0.0 <= pnl_pct <= 10.0:
                reasons.append(f"損益率 {pnl_pct:.2f}%")
            if item["annual_div"] > 0:
                reasons.append(f"年間配当 {item['annual_div']:,.0f}円")
            continue_items.append(_make_item_obj(item, score, reasons, "継続候補"))

        # 現引
        if item["can_margin_to_spot"] and (
            (days is not None and days >= 7)
            or item["annual_div"] > 0
            or item["pnl"] >= 0
        ):
            score = 45
            if days is not None:
                score += min(15, int(days / 7))
            if item["annual_div"] > 0:
                score += 10
            reasons = []
            if days is not None and days >= 7:
                reasons.append(f"信用保有 {days}日")
            if item["annual_div"] > 0:
                reasons.append(f"年間配当 {item['annual_div']:,.0f}円")
            if item["pnl"] >= 0:
                reasons.append("含み益側")
            margin_to_spot.append(_make_item_obj(item, score, reasons, "現引候補"))

        # NISA
        if account_key == "SPEC" and (
            item["annual_div"] > 0
            or (item["yield_cost"] is not None and item["yield_cost"] >= 2.0)
        ):
            score = 42
            if item["yield_cost"] is not None:
                score += min(15, int(item["yield_cost"]))
            if item["annual_div"] > 0:
                score += min(10, int(item["annual_div"] / 10000))
            reasons = []
            if item["annual_div"] > 0:
                reasons.append(f"年間配当 {item['annual_div']:,.0f}円")
            if item["yield_cost"] is not None:
                reasons.append(f"取得利回り {item['yield_cost']:.2f}%")
            nisa_items.append(_make_item_obj(item, score, reasons, "NISA候補"))

        # 慎重
        if (
            (ratio is not None and ratio >= 25.0)
            or (account_key == "MARGIN" and days is not None and days >= 30)
            or (item["pnl"] < 0 and days is not None and days >= 60)
        ):
            score = 50
            if ratio is not None and ratio >= 25.0:
                score += min(20, int(ratio))
            if account_key == "MARGIN" and days is not None and days >= 30:
                score += min(15, int(days / 10))
            if item["pnl"] < 0 and days is not None and days >= 60:
                score += 12
            reasons = []
            if ratio is not None and ratio >= 25.0:
                reasons.append(f"構成比 {ratio:.1f}%")
            if account_key == "MARGIN" and days is not None and days >= 30:
                reasons.append(f"信用保有 {days}日")
            if item["pnl"] < 0 and days is not None and days >= 60:
                reasons.append("長期含み損")
            cautious.append(_make_item_obj(item, score, reasons, "慎重候補"))

    overall_map: dict[int, dict[str, Any]] = {}

    def _merge_from(src: list[dict[str, Any]]):
        for item in src:
            hid = int(item["holding_id"])
            if hid not in overall_map:
                overall_map[hid] = {
                    **item,
                    "score": 0,
                    "reasons": [],
                    "reason_text": "",
                    "badges": [],
                    "suggestion": "総合候補",
                }
            overall_map[hid]["score"] += int(item["score"] or 0)
            for reason in item["reasons"]:
                if reason not in overall_map[hid]["reasons"]:
                    overall_map[hid]["reasons"].append(reason)

    for src in (take_profit, stop_alert, continue_items, margin_to_spot, nisa_items, cautious):
        _merge_from(src)

    overall = []
    for item in overall_map.values():
        item["reason_text"] = " / ".join(item["reasons"])
        item["badges"] = [
            {"kind": "suggestion", "label": "総合候補"},
            {"kind": "score", "label": f"優先 {item['score']}"},
        ]
        overall.append(item)

    take_profit.sort(key=lambda x: x["score"], reverse=True)
    stop_alert.sort(key=lambda x: x["score"], reverse=True)
    continue_items.sort(key=lambda x: x["score"], reverse=True)
    margin_to_spot.sort(key=lambda x: x["score"], reverse=True)
    nisa_items.sort(key=lambda x: x["score"], reverse=True)
    cautious.sort(key=lambda x: x["score"], reverse=True)
    overall.sort(key=lambda x: x["score"], reverse=True)

    return {
        "counts": {
            "overall": len(overall),
            "take_profit": len(take_profit),
            "stop_alert": len(stop_alert),
            "continue": len(continue_items),
            "margin_to_spot": len(margin_to_spot),
            "nisa": len(nisa_items),
            "cautious": len(cautious),
        },
        "groups": {
            "overall": overall[:20],
            "take_profit": take_profit[:20],
            "stop_alert": stop_alert[:20],
            "continue": continue_items[:20],
            "margin_to_spot": margin_to_spot[:20],
            "nisa": nisa_items[:20],
            "cautious": cautious[:20],
        },
    }


def _make_item_obj(item: dict[str, Any], score: int, reasons: list[str], suggestion: str) -> dict[str, Any]:
    copied = dict(item)
    copied["score"] = score
    copied["reasons"] = reasons
    copied["reason_text"] = " / ".join(reasons)
    copied["suggestion"] = suggestion
    copied["badges"] = [
        {"kind": "suggestion", "label": suggestion},
        {"kind": "score", "label": f"優先 {score}"},
    ]
    if copied.get("ratio") is not None:
        copied["badges"].append({"kind": "ratio", "label": f"構成 {copied['ratio']:.1f}%"})
    copied["badges"] = copied["badges"][:3]
    return copied


def build_ai_context(rows, active_broker: str, active_scope: str, active_category: str) -> dict[str, Any]:
    broker_totals: dict[str, dict[str, int]] = {}
    for broker_key, _label in BROKER_ORDER:
        broker_totals[broker_key] = {"all": 0, "spot": 0, "margin": 0}

    for r in rows:
        h = r.obj
        broker_key = (getattr(h, "broker", "") or "").upper()
        account_key = _account_key(h)

        broker_totals["ALL"]["all"] += 1
        if account_key in ("SPEC", "NISA"):
            broker_totals["ALL"]["spot"] += 1
        if account_key == "MARGIN":
            broker_totals["ALL"]["margin"] += 1

        if broker_key in broker_totals:
            broker_totals[broker_key]["all"] += 1
            if account_key in ("SPEC", "NISA"):
                broker_totals[broker_key]["spot"] += 1
            if account_key == "MARGIN":
                broker_totals[broker_key]["margin"] += 1

    broker_tabs = []
    for key, label in BROKER_ORDER:
        counts = broker_totals.get(key, {"all": 0, "spot": 0, "margin": 0})
        broker_tabs.append(
            {
                "key": key,
                "label": label,
                "count": counts["all"],
                "counts": counts,
                "is_active": key == active_broker,
            }
        )

    active_scope_counts = broker_totals.get(active_broker, {"all": 0, "spot": 0, "margin": 0})
    scope_tabs = []
    for key, label in SCOPE_ORDER:
        scope_tabs.append(
            {
                "key": key,
                "label": label,
                "count": int(active_scope_counts.get(key, 0)),
                "is_active": key == active_scope,
            }
        )

    combo_panels = []
    active_combo_counts = {
        "overall": 0,
        "take_profit": 0,
        "stop_alert": 0,
        "continue": 0,
        "margin_to_spot": 0,
        "nisa": 0,
        "cautious": 0,
    }

    for broker_key, _broker_label in BROKER_ORDER:
        broker_rows = [r for r in rows if _broker_match(r.obj, broker_key)]

        for scope_key, _scope_label in SCOPE_ORDER:
            filtered = [r for r in broker_rows if _scope_match(r.obj, scope_key)]
            built = _build_groups(filtered)

            combo = {
                "broker_key": broker_key,
                "scope_key": scope_key,
                "count": len(filtered),
                "counts": built["counts"],
                "groups": built["groups"],
            }
            combo_panels.append(combo)

            if broker_key == active_broker and scope_key == active_scope:
                active_combo_counts = built["counts"]

    category_tabs = []
    for key, label in CATEGORY_ORDER:
        category_tabs.append(
            {
                "key": key,
                "label": label,
                "count": int(active_combo_counts.get(key, 0)),
                "is_active": key == active_category,
            }
        )

    return {
        "active_broker": active_broker,
        "active_scope": active_scope,
        "active_category": active_category,
        "broker_tabs": broker_tabs,
        "scope_tabs": scope_tabs,
        "category_tabs": category_tabs,
        "combo_panels": combo_panels,
    }