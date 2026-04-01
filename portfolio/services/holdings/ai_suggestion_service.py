# [FILE] ai_suggestion_service.py
# [PATH] portfolio/services/holdings/ai_suggestion_service.py
#
# このファイルは何？
# - /holdings/ai/ 専用の提案サービス
# - 保有RowVMから、AI提案ページ用の候補を作る
#
# 今回の方針
# - ルールベースを土台にしつつ、
#   「AIが保有全体を見て1銘柄1結論で話してくる」形に寄せる
# - 証券会社 × 対象(全体/現物/信用) ごとに切り替えられる
# - AI総括 / 優先順位 / フレンドリー自然文コメントを作る

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
    ("overall", "AIの本音"),
    ("take_profit", "勝ちを崩す前に触る"),
    ("stop_alert", "甘やかすと危ない"),
    ("continue", "まだ降りなくていい"),
    ("margin_to_spot", "置き場所を変える"),
    ("nisa", "長く持つならこっち"),
    ("cautious", "今日は触りすぎない"),
]

CATEGORY_NOTES = {
    "overall": "AIがポートフォリオ全体を見て、今触る順番で並べています。",
    "take_profit": "利益が乗っているうちに、一部でも身軽にしたい枠です。",
    "stop_alert": "期待で引っ張るより、資金効率を優先したい枠です。",
    "continue": "今は無理にいじらず、素直に継続で見たい枠です。",
    "margin_to_spot": "短期の建玉というより、置き場所を変えたほうが綺麗な枠です。",
    "nisa": "残す前提で見るなら、特定よりNISAが似合いやすい枠です。",
    "cautious": "勢いで触ると逆に崩しやすいので、サイズ感から慎重に見たい枠です。",
}


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


def _fmt_yen(v: float) -> str:
    try:
        return f"{int(round(v)):,.0f}円"
    except Exception:
        return "—"


def _fmt_signed_yen(v: float) -> str:
    try:
        n = int(round(v))
        if n > 0:
            return f"+{n:,.0f}円"
        if n < 0:
            return f"-{abs(n):,.0f}円"
        return "0円"
    except Exception:
        return "—"


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}%"
    except Exception:
        return "—"


def _pick(seed: int, options: list[str]) -> str:
    if not options:
        return ""
    idx = seed % len(options)
    return options[idx]


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


def _build_base_item(row, total_val: float) -> dict[str, Any]:
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

    return {
        "holding_id": int(getattr(h, "id", 0) or 0),
        "ticker": _safe_text(getattr(h, "ticker", None)),
        "name": _safe_text(getattr(h, "name", None)),
        "broker": _broker_label(h),
        "account": _account_label(h),
        "account_key": _account_key(h),
        "qty": qty,
        "valuation": valuation,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "days": days,
        "annual_div": annual_div,
        "yield_cost": yield_cost,
        "ratio": ratio,
        "can_margin_to_spot": can_margin_to_spot,
    }


def _score_take_profit(item: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    pnl = item["pnl"]
    pnl_pct = item["pnl_pct"]
    ratio = item["ratio"]
    days = item["days"]

    if pnl <= 0:
        return 0, reasons

    score += 18

    if pnl_pct is not None and pnl_pct >= 8.0:
        score += min(28, int(round(pnl_pct * 1.8)))
        reasons.append(f"損益率 {pnl_pct:.2f}%")
    if ratio is not None and ratio >= 16.0:
        score += min(24, int(round((ratio - 15.0) * 2.0)))
        reasons.append(f"構成比 {ratio:.1f}%")
    if days is not None and days >= 30:
        score += min(12, int(days / 10))
        reasons.append(f"保有 {days}日")

    return score, reasons


def _score_stop_alert(item: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    pnl = item["pnl"]
    pnl_pct = item["pnl_pct"]
    ratio = item["ratio"]
    days = item["days"]
    account_key = item["account_key"]

    if pnl >= 0:
        return 0, reasons

    score += 20

    if pnl_pct is not None:
        if pnl_pct <= -4.0:
            score += min(34, int(round(abs(pnl_pct) * 2.7)))
            reasons.append(f"損益率 {pnl_pct:.2f}%")
        elif pnl_pct < 0:
            score += min(10, int(round(abs(pnl_pct) * 1.5)))

    if account_key == "MARGIN":
        score += 14
        reasons.append("信用保有")

    if days is not None and days >= 25:
        score += min(16, int(days / 12))
        reasons.append(f"保有 {days}日")

    if ratio is not None and ratio >= 12.0:
        score += min(10, int(ratio / 3))

    return score, reasons


def _score_continue(item: dict[str, Any]) -> tuple[int, list[str]]:
    score = 12
    reasons: list[str] = []

    pnl = item["pnl"]
    pnl_pct = item["pnl_pct"]
    ratio = item["ratio"]
    annual_div = item["annual_div"]
    days = item["days"]

    if pnl >= 0:
        score += 16
        reasons.append("大きく崩れていない")

    if pnl_pct is not None and -3.0 <= pnl_pct <= 12.0:
        score += 12
        reasons.append(f"損益率 {_fmt_pct(pnl_pct)}")

    if annual_div > 0:
        score += 8
        reasons.append(f"年間配当 {_fmt_yen(annual_div)}")

    if ratio is not None and ratio <= 15.0:
        score += 6

    if days is not None and days <= 45:
        score += 4

    return score, reasons


def _score_margin_to_spot(item: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if not item["can_margin_to_spot"]:
        return 0, reasons

    score += 24
    reasons.append("信用買い建玉")

    days = item["days"]
    annual_div = item["annual_div"]
    pnl = item["pnl"]

    if days is not None and days >= 7:
        score += min(18, int(days / 5))
        reasons.append(f"信用で {days}日")

    if annual_div > 0:
        score += min(18, int(annual_div / 10000))
        reasons.append(f"年間配当 {_fmt_yen(annual_div)}")

    if pnl >= 0:
        score += 8
        reasons.append("含み益側")

    return score, reasons


def _score_nisa(item: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if item["account_key"] != "SPEC":
        return 0, reasons

    annual_div = item["annual_div"]
    yield_cost = item["yield_cost"]
    days = item["days"]
    pnl = item["pnl"]

    if annual_div <= 0 and (yield_cost is None or yield_cost < 1.5):
        return 0, reasons

    score += 16
    reasons.append("特定口座")

    if annual_div > 0:
        score += min(18, int(annual_div / 10000))
        reasons.append(f"年間配当 {_fmt_yen(annual_div)}")

    if yield_cost is not None and yield_cost >= 1.5:
        score += min(18, int(round(yield_cost * 4)))
        reasons.append(f"取得利回り {yield_cost:.2f}%")

    if days is not None and days >= 20:
        score += min(8, int(days / 20))

    if pnl >= 0:
        score += 4

    return score, reasons


def _score_cautious(item: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    ratio = item["ratio"]
    days = item["days"]
    pnl = item["pnl"]
    account_key = item["account_key"]

    if ratio is not None and ratio >= 25.0:
        score += min(32, int(round(ratio * 1.2)))
        reasons.append(f"構成比 {ratio:.1f}%")

    if account_key == "MARGIN" and days is not None and days >= 30:
        score += min(22, int(days / 8))
        reasons.append(f"信用で {days}日")

    if pnl < 0 and days is not None and days >= 60:
        score += 14
        reasons.append("長期含み損")

    return score, reasons


def _decide_action(item: dict[str, Any]) -> dict[str, Any]:
    score_map: dict[str, tuple[int, list[str]]] = {
        "take_profit": _score_take_profit(item),
        "stop_alert": _score_stop_alert(item),
        "continue": _score_continue(item),
        "margin_to_spot": _score_margin_to_spot(item),
        "nisa": _score_nisa(item),
        "cautious": _score_cautious(item),
    }

    best_key = "continue"
    best_score = -1
    best_reasons: list[str] = []

    for key, (score, reasons) in score_map.items():
        if score > best_score:
            best_key = key
            best_score = score
            best_reasons = reasons

    if best_score < 0:
        best_key = "continue"
        best_score = 18
        best_reasons = ["今日は無理に触らなくてよさそう"]

    if best_key == "continue" and best_score < 24:
        best_reasons = ["今日は無理にいじらなくてよさそう"]

    return {
        "action_key": best_key,
        "action_score": int(best_score),
        "action_reasons": best_reasons,
    }


def _urgency_label(action_key: str, score: int) -> str:
    if action_key in ("stop_alert", "take_profit") and score >= 68:
        return "今すぐ"
    if action_key in ("margin_to_spot", "nisa") and score >= 56:
        return "近いうち"
    if score >= 58:
        return "近いうち"
    return "様子見"


def _action_label(action_key: str) -> str:
    return dict(CATEGORY_ORDER).get(action_key, "AI判断")


def _ai_title(item: dict[str, Any], action_key: str) -> str:
    seed = int(item["holding_id"] or 0)

    mapping = {
        "take_profit": [
            "勝ってるうちに一度軽くしたい",
            "ここは欲張りすぎず利食い寄り",
            "利益を守る動きが先に見える",
        ],
        "stop_alert": [
            "この子はそろそろ甘やかしたくない",
            "まだ引っ張るより傷を浅く見たい",
            "延命より整理を考えたい",
        ],
        "continue": [
            "まだ降りる理由は薄い",
            "今は素直に継続でいい",
            "ここは無理にいじらなくてよさそう",
        ],
        "margin_to_spot": [
            "信用のままより置き場所を変えたい",
            "現引するとかなりしっくりくる",
            "このまま信用で持つのは少しノイズ",
        ],
        "nisa": [
            "長く持つならNISA側が似合う",
            "この子はNISAに寄せるときれい",
            "置き場所を変える価値がある",
        ],
        "cautious": [
            "今日は触り方をかなり慎重にしたい",
            "勢いでいじるとやや危ない",
            "見た目以上に重たい枠かも",
        ],
    }
    return _pick(seed, mapping.get(action_key, ["AIが見直し候補と判断"])) or "AIが見直し候補と判断"


def _ai_trigger(item: dict[str, Any], action_key: str) -> str:
    pnl_pct = _fmt_pct(item["pnl_pct"])
    ratio = "—" if item["ratio"] is None else f"{item['ratio']:.1f}%"
    days = "—" if item["days"] is None else f"{item['days']}日"
    annual_div = _fmt_yen(item["annual_div"])

    if action_key == "take_profit":
        return f"AIが今見ているのは、損益率 {pnl_pct} と構成比 {ratio} の重なり。"
    if action_key == "stop_alert":
        return f"AIが今気にしているのは、損益率 {pnl_pct} と保有 {days} の重さ。"
    if action_key == "margin_to_spot":
        return f"AIが今見ているのは、信用保有 {days} と年間配当 {annual_div} の組み合わせ。"
    if action_key == "nisa":
        return f"AIが今見ているのは、年間配当 {annual_div} と取得利回り {_fmt_pct(item['yield_cost'])}。"
    if action_key == "cautious":
        return f"AIが今気にしているのは、構成比 {ratio} と保有 {days} の偏り。"
    return f"AIが今見ているのは、損益率 {pnl_pct} と全体の中での重さのバランス。"


def _ai_comment(item: dict[str, Any], action_key: str) -> str:
    pnl = _fmt_signed_yen(item["pnl"])
    pnl_pct = _fmt_pct(item["pnl_pct"])
    ratio = "—" if item["ratio"] is None else f"{item['ratio']:.1f}%"
    days = "—" if item["days"] is None else f"{item['days']}日"
    annual_div = _fmt_yen(item["annual_div"])
    yield_cost = _fmt_pct(item["yield_cost"])

    if action_key == "take_profit":
        return (
            f"この銘柄、悪くないどころか今はちゃんと勝ってる枠です。"
            f" ただ、含み損益 {pnl}・損益率 {pnl_pct} まで来ていて、しかも全体の {ratio} を占めるなら、"
            f"次にやることは“さらに追う”より“崩す前に少し守る”のほうが上手いです。"
        )

    if action_key == "stop_alert":
        return (
            f"いちばん気になるのは、含み損益 {pnl} そのものより、"
            f"{days} 使っているのに改善の手触りが弱いことです。"
            f" ここは期待で延命するより、資金効率で冷静に見たほうがいい枠です。"
        )

    if action_key == "margin_to_spot":
        return (
            f"この子は短期の建玉として持つより、現引して置き場所を変えたほうが自然です。"
            f" {days} 持っていて年間配当 {annual_div} が見えているなら、"
            f"もう“信用で抱える理由”より“どこに置くか”の話になっています。"
        )

    if action_key == "nisa":
        return (
            f"これは売買のネタというより、残す前提で見たほうがしっくりくる銘柄です。"
            f" 年間配当 {annual_div}、取得利回り {yield_cost} まで見えるなら、"
            f"特定に置いたままなのが少しもったいないです。"
        )

    if action_key == "cautious":
        return (
            f"一見ふつうに見えても、全体で見るとちょっと重たいです。"
            f" 構成比 {ratio} や保有 {days} が効いてきていて、"
            f"“触るかどうか”より“どう触るか”を慎重に決めたい枠です。"
        )

    return (
        f"今のこの銘柄は、無理に振り回さなくていいです。"
        f" 損益率 {pnl_pct}、保有 {days}、配当 {annual_div} のバランスを見ると、"
        f"今日は監視継続くらいがいちばん自然に見えます。"
    )


def _ai_counter(item: dict[str, Any], action_key: str) -> str:
    if action_key == "take_profit":
        return "ただ、トレンド自体が完全に崩れたわけではないので、全部じゃなく一部で十分です。"
    if action_key == "stop_alert":
        return "ただ、ルールがあるならそのラインは守りたいです。感情で引き伸ばすのだけは避けたいです。"
    if action_key == "margin_to_spot":
        return "ただ、現引したあとに放置化しやすいので、“持つ理由”は一言で言える状態にしておきたいです。"
    if action_key == "nisa":
        return "ただ、すぐ触る可能性が少しでもあるなら、急いで場所だけ変える必要はありません。"
    if action_key == "cautious":
        return "ただ、今すぐ投げると決めるほどではありません。雑に触るのがいちばん危ないです。"
    return "ただ、放置ではなく監視継続です。構成比や損益率が跳ねたら一気に話が変わります。"


def _metric_rows(item: dict[str, Any], action_key: str) -> list[dict[str, str]]:
    rows = [
        {
            "label": "評価額",
            "value": f"¥{int(round(item['valuation'])):,}",
            "class": "",
        },
        {
            "label": "含み損益",
            "value": f"¥{int(round(item['pnl'])):,}",
            "class": "neg" if item["pnl"] < 0 else "pos",
        },
        {
            "label": "損益率",
            "value": _fmt_pct(item["pnl_pct"]),
            "class": "neg" if item["pnl_pct"] is not None and item["pnl_pct"] < 0 else "pos",
        },
        {
            "label": "構成比",
            "value": "—" if item["ratio"] is None else f"{item['ratio']:.1f}%",
            "class": "",
        },
        {
            "label": "保有日数",
            "value": "—" if item["days"] is None else f"{item['days']}日",
            "class": "",
        },
    ]

    if action_key == "nisa":
        rows.append(
            {
                "label": "取得利回り",
                "value": _fmt_pct(item["yield_cost"]),
                "class": "",
            }
        )
    else:
        rows.append(
            {
                "label": "年間配当",
                "value": _fmt_yen(item["annual_div"]),
                "class": "",
            }
        )

    return rows


def _badge_list(item: dict[str, Any], action_label: str, urgency_label: str) -> list[dict[str, str]]:
    badges: list[dict[str, str]] = [
        {"kind": "suggestion", "label": action_label},
        {"kind": "urgency", "label": urgency_label},
    ]
    if item["ratio"] is not None:
        badges.append({"kind": "ratio", "label": f"構成 {item['ratio']:.1f}%"})
    return badges[:3]


def _decorate_item(base_item: dict[str, Any]) -> dict[str, Any]:
    decided = _decide_action(base_item)
    action_key = decided["action_key"]
    score = int(decided["action_score"])
    action_label = _action_label(action_key)
    urgency = _urgency_label(action_key, score)

    item = dict(base_item)
    item["action_key"] = action_key
    item["action_label"] = action_label
    item["priority_score"] = score
    item["urgency_label"] = urgency
    item["reasons"] = decided["action_reasons"]
    item["reason_text"] = " / ".join(decided["action_reasons"])
    item["ai_title"] = _ai_title(item, action_key)
    item["ai_trigger"] = _ai_trigger(item, action_key)
    item["ai_comment"] = _ai_comment(item, action_key)
    item["ai_counter"] = _ai_counter(item, action_key)
    item["metric_rows"] = _metric_rows(item, action_key)
    item["badges"] = _badge_list(item, action_label, urgency)
    return item


def _brief_headline(counts: dict[str, int]) -> str:
    danger_count = counts["stop_alert"] + counts["cautious"]
    relocate_count = counts["margin_to_spot"] + counts["nisa"]

    if danger_count >= max(2, counts["take_profit"]):
        return "今日は増やす日というより、危ない枠を軽くしたい日です。"
    if counts["take_profit"] >= 2:
        return "今日は勝ちを伸ばすより、利益を崩さない整え方がハマりそうです。"
    if relocate_count >= 2:
        return "今日は銘柄選びより、置き場所を整えるほうが効きそうです。"
    if counts["continue"] >= max(counts["take_profit"], counts["stop_alert"], counts["cautious"]):
        return "今日は無理に触りすぎず、強い子だけ残す目線で十分そうです。"
    return "今日は全体を軽く点検しつつ、触るなら少数でよさそうです。"


def _brief_summary(items: list[dict[str, Any]], counts: dict[str, int]) -> str:
    total_val = sum(float(x["valuation"]) for x in items)
    total_pnl = sum(float(x["pnl"]) for x in items)
    total_pnl_pct = _pct(total_pnl, sum(max(float(x["valuation"]) - float(x["pnl"]), 0.0) for x in items))

    main = f"保有 {len(items)} 件、評価額合計は {_fmt_yen(total_val)}、含み損益は {_fmt_signed_yen(total_pnl)}。"
    if total_pnl_pct is not None:
        main += f" ざっくり見ると全体の温度感は {_fmt_pct(total_pnl_pct)} です。"

    extra_parts = []
    if counts["stop_alert"] > 0:
        extra_parts.append(f"甘やかすと危ない枠が {counts['stop_alert']} 件")
    if counts["take_profit"] > 0:
        extra_parts.append(f"勝っているうちに軽くしたい枠が {counts['take_profit']} 件")
    if counts["margin_to_spot"] + counts["nisa"] > 0:
        extra_parts.append(f"置き場所を変えるだけで見え方が良くなる枠が {counts['margin_to_spot'] + counts['nisa']} 件")

    if extra_parts:
        main += " 今の本題は、" + "、".join(extra_parts) + " あることです。"

    return main


def _brief_focus_points(counts: dict[str, int]) -> list[str]:
    lines: list[str] = []

    if counts["take_profit"] > 0:
        lines.append(f"勝ってる枠は {counts['take_profit']} 件。伸ばすより、まず崩さない動きが効きそう。")
    if counts["stop_alert"] > 0:
        lines.append(f"危ない枠は {counts['stop_alert']} 件。期待で延命するより、整理の順番を決めたい。")
    if counts["margin_to_spot"] > 0:
        lines.append(f"現引したほうが自然な枠が {counts['margin_to_spot']} 件。短期建玉の顔をしていないです。")
    if counts["nisa"] > 0:
        lines.append(f"NISA寄せを考えたい枠が {counts['nisa']} 件。銘柄選びより置き場所の問題です。")
    if counts["continue"] > 0 and len(lines) < 3:
        lines.append(f"無理に触らなくていい枠も {counts['continue']} 件。全部いじる日ではなさそうです。")

    if not lines:
        lines.append("今は大きく崩れている保有は少なく、触るとしても少数で十分そうです。")

    return lines[:3]


def _brief_top_actions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top = []
    for item in items[:3]:
        top.append(
            {
                "name": item["name"],
                "ticker": item["ticker"],
                "action_label": item["action_label"],
                "urgency_label": item["urgency_label"],
                "line": item["ai_title"],
            }
        )
    return top


def _build_brief(items: list[dict[str, Any]], counts: dict[str, int]) -> dict[str, Any]:
    now_count = sum(1 for x in items if x["urgency_label"] == "今すぐ")
    relocate_count = counts["margin_to_spot"] + counts["nisa"]
    danger_count = counts["stop_alert"] + counts["cautious"]

    return {
        "headline": _brief_headline(counts),
        "summary": _brief_summary(items, counts),
        "focus_points": _brief_focus_points(counts),
        "top_actions": _brief_top_actions(items),
        "now_count": now_count,
        "relocate_count": relocate_count,
        "danger_count": danger_count,
    }


def _build_groups(rows) -> dict[str, Any]:
    total_val = sum(_to_float(getattr(r, "valuation", None), 0.0) for r in rows)

    items = [_decorate_item(_build_base_item(r, total_val=total_val)) for r in rows]
    items.sort(key=lambda x: (x["priority_score"], x["valuation"]), reverse=True)

    grouped = {
        "overall": list(items),
        "take_profit": [],
        "stop_alert": [],
        "continue": [],
        "margin_to_spot": [],
        "nisa": [],
        "cautious": [],
    }

    for item in items:
        grouped[item["action_key"]].append(item)

    counts = {
        "overall": len(grouped["overall"]),
        "take_profit": len(grouped["take_profit"]),
        "stop_alert": len(grouped["stop_alert"]),
        "continue": len(grouped["continue"]),
        "margin_to_spot": len(grouped["margin_to_spot"]),
        "nisa": len(grouped["nisa"]),
        "cautious": len(grouped["cautious"]),
    }

    category_panels = []
    for key, label in CATEGORY_ORDER:
        category_panels.append(
            {
                "key": key,
                "label": label,
                "note": CATEGORY_NOTES.get(key, ""),
                "items": grouped[key][:20],
                "count": counts[key],
            }
        )

    return {
        "counts": counts,
        "groups": {k: v[:20] for k, v in grouped.items()},
        "category_panels": category_panels,
        "brief": _build_brief(items, counts),
    }


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
                "category_panels": built["category_panels"],
                "brief": built["brief"],
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