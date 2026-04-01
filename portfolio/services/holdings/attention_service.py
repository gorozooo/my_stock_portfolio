# [FILE] attention_service.py
# [PATH] portfolio/services/holdings/attention_service.py
#
# このファイルは何？
# - /holdings/attention/ 専用の集計サービス
# - 保有RowVMから「要注意」ページ用の材料を作る
#
# 今回の方針
# - タブ切替は再読込なし
# - 証券会社 × 対象(全体/現物/信用) ごとに、
#   含み損 / 信用長期化 / 集中 / 放置 / 配当非効率 / 総合 を作る
# - まずは実務的に使える判定を優先する

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
    ("loss", "含み損"),
    ("margin_long", "信用長期化"),
    ("concentration", "集中"),
    ("stale", "放置"),
    ("dividend", "配当非効率"),
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


def _make_item(row, total_val: float, score: Optional[int] = None, reasons: Optional[list[str]] = None) -> dict[str, Any]:
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

    reason_list = reasons or []
    reason_text = " / ".join(reason_list) if reason_list else ""

    badges: list[dict[str, str]] = []
    if score is not None:
        badges.append({"kind": "score", "label": f"注意 {score}"})
    if ratio is not None and ratio > 0:
        badges.append({"kind": "ratio", "label": f"構成 {ratio:.1f}%"})
    if days is not None:
        badges.append({"kind": "days", "label": f"{days}日"})

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
        "badges": badges[:2],
    }


def _build_groups(rows) -> dict[str, Any]:
    total_val = sum(_to_float(getattr(r, "valuation", None), 0.0) for r in rows)

    base_items = [_make_item(r, total_val=total_val) for r in rows]

    loss_items = [x for x in base_items if x["pnl"] < 0]
    loss_items.sort(key=lambda x: (x["pnl"], x["pnl_pct"] if x["pnl_pct"] is not None else 9999.0))

    margin_long_items = [
        x for x in base_items
        if x["account_key"] == "MARGIN" and x["days"] is not None and x["days"] >= 21
    ]
    margin_long_items.sort(key=lambda x: (x["days"] or 0, -x["pnl"]), reverse=True)

    concentration_items = [
        x for x in base_items
        if x["ratio"] is not None and x["ratio"] >= 20.0
    ]
    concentration_items.sort(key=lambda x: x["ratio"] or 0.0, reverse=True)

    stale_items = [
        x for x in base_items
        if x["days"] is not None and x["days"] >= 60
    ]
    stale_items.sort(key=lambda x: x["days"] or 0, reverse=True)

    dividend_items = [
        x for x in base_items
        if x["account_key"] == "SPEC" and x["annual_div"] > 0
    ]
    dividend_items.sort(key=lambda x: (x["annual_div"], x["yield_cost"] or 0.0), reverse=True)

    overall_map: dict[int, dict[str, Any]] = {}

    def _ensure(row_item: dict[str, Any]) -> dict[str, Any]:
        hid = int(row_item["holding_id"])
        if hid not in overall_map:
            overall_map[hid] = {
                **row_item,
                "score": 0,
                "reasons": [],
                "reason_text": "",
                "badges": [],
            }
        return overall_map[hid]

    for x in loss_items:
        item = _ensure(x)
        add = 10
        if x["pnl_pct"] is not None:
            add += min(25, int(abs(x["pnl_pct"])))
        item["score"] += add
        if "含み損" not in item["reasons"]:
            item["reasons"].append("含み損")

    for x in margin_long_items:
        item = _ensure(x)
        add = 20 + min(15, int((x["days"] or 0) / 7))
        item["score"] += add
        if "信用長期化" not in item["reasons"]:
            item["reasons"].append("信用長期化")

    for x in concentration_items:
        item = _ensure(x)
        add = 18
        if x["ratio"] is not None:
            add += min(15, int(x["ratio"] - 20))
        item["score"] += add
        if "集中" not in item["reasons"]:
            item["reasons"].append("集中")

    for x in stale_items:
        item = _ensure(x)
        add = 10 + min(10, int((x["days"] or 0) / 30))
        item["score"] += add
        if "放置" not in item["reasons"]:
            item["reasons"].append("放置")

    for x in dividend_items:
        item = _ensure(x)
        item["score"] += 8
        if "配当非効率" not in item["reasons"]:
            item["reasons"].append("配当非効率")

    overall_items = []
    for item in overall_map.values():
        item["reason_text"] = " / ".join(item["reasons"])
        badges: list[dict[str, str]] = []
        badges.append({"kind": "score", "label": f"注意 {item['score']}"})
        if item["ratio"] is not None:
            badges.append({"kind": "ratio", "label": f"構成 {item['ratio']:.1f}%"})
        item["badges"] = badges[:2]
        overall_items.append(item)

    overall_items.sort(key=lambda x: (x["score"], -(x["pnl"])), reverse=True)

    return {
        "counts": {
            "overall": len(overall_items),
            "loss": len(loss_items),
            "margin_long": len(margin_long_items),
            "concentration": len(concentration_items),
            "stale": len(stale_items),
            "dividend": len(dividend_items),
        },
        "groups": {
            "overall": overall_items[:20],
            "loss": loss_items[:20],
            "margin_long": margin_long_items[:20],
            "concentration": concentration_items[:20],
            "stale": stale_items[:20],
            "dividend": dividend_items[:20],
        },
    }


def build_attention_context(rows, active_broker: str, active_scope: str, active_category: str) -> dict[str, Any]:
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
        "loss": 0,
        "margin_long": 0,
        "concentration": 0,
        "stale": 0,
        "dividend": 0,
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