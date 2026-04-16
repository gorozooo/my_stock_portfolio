# =========================================================
# [FILE] notify_builder.py
# [PATH] <project_root>/tradeai/services/holdings/notify_builder.py
#
# このファイルは何？
# - 保有監視の結果を、LINE通知用の見やすい形へ整えるサービスです。
# - テキスト通知の文面だけでなく、Flex Message のカードも作ります。
# - 「根拠」と「どう見るか」を分けて、初心者でも読みやすい構成にします。
# =========================================================

from __future__ import annotations

from typing import Iterable

from django.utils import timezone


def build_holding_notify_dedupe_key(user_id: int, row: dict) -> str:
    ticker = row.get("ticker") or ""
    direction = row.get("direction") or "NONE"
    action = row.get("action_key") or ""
    level = row.get("level") or ""
    pnl_pct = row.get("pnl_pct")
    pnl_bucket = "na" if pnl_pct is None else str(int(float(pnl_pct)))
    return f"holding:{user_id}:{ticker}:{direction}:{action}:{level}:{pnl_bucket}"


def _headline(rows: list[dict]) -> str:
    strong_count = sum(1 for row in rows if row.get("level") == "STRONG")
    attention_count = sum(1 for row in rows if row.get("level") == "ATTENTION")

    if strong_count > 0:
        return f"TradeAI 保有通知\n強い警戒 {strong_count}件 / 注意 {attention_count}件"
    return f"TradeAI 保有通知\n注意 {attention_count}件"


def _direction_label(row: dict) -> str:
    direction = row.get("direction")
    if direction == "LONG":
        return "ロング保有"
    if direction == "SHORT":
        return "ショート保有"
    return "保有"


def _level_label(row: dict) -> str:
    level = row.get("level")
    if level == "STRONG":
        return "強い警戒"
    if level == "ATTENTION":
        return "注意"
    return "参考"


def _format_price(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return "-"


def _format_pct(value) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}%"


def _format_yen(value) -> str:
    if value is None:
        return "-"
    return f"{int(round(float(value))):,}円"


def _flow_label_from_row(row: dict) -> str:
    ma_state = row.get("ma_state")
    if ma_state == "GOLDEN_CROSS":
        return "上向きに転換"
    if ma_state == "ABOVE_GC":
        return "上向き"
    if ma_state == "DEAD_CROSS":
        return "下向きに転換"
    if ma_state == "BELOW_DC":
        return "下向き"
    return "まだ不明"


def _volatility_label_from_row(row: dict) -> str:
    atr_pct = row.get("atr_pct")
    if atr_pct is None:
        return "まだ不明"
    if float(atr_pct) >= 4:
        return "大きい"
    if float(atr_pct) >= 2:
        return "やや大きめ"
    if float(atr_pct) >= 1:
        return "ふつう"
    return "小さめ"


def _reason_candidates(row: dict) -> list[str]:
    reasons: list[str] = []

    pnl_pct = row.get("pnl_pct")
    if pnl_pct is not None:
        if float(pnl_pct) >= 0:
            reasons.append(f"含み益 {_format_pct(pnl_pct)}")
        else:
            reasons.append(f"含み損 {_format_pct(pnl_pct)}")

    flow_label = _flow_label_from_row(row)
    if flow_label != "まだ不明":
        reasons.append(f"流れ {flow_label}")

    vol_label = _volatility_label_from_row(row)
    if vol_label != "まだ不明":
        reasons.append(f"値動き {vol_label}")

    hold_days = row.get("hold_days")
    if hold_days is not None:
        reasons.append(f"保有 {int(hold_days)}日")

    account_label = row.get("account_label") or ""
    if account_label:
        reasons.append(f"{account_label}口座")

    cleaned: list[str] = []
    for reason in reasons:
        reason = str(reason).strip()
        if reason and reason not in cleaned:
            cleaned.append(reason)

    return cleaned[:3]


def _decision_text(row: dict) -> str:
    action_key = row.get("action_key") or ""
    direction = row.get("direction") or ""
    flow_label = _flow_label_from_row(row)
    volatility = _volatility_label_from_row(row)
    hold_days = row.get("hold_days")
    account = row.get("account") or ""

    if action_key == "STOP_LOSS_WARNING":
        if direction == "LONG":
            text = "損失が大きく、まず見直し優先です。戻り待ちよりリスク管理を優先したいです。"
        else:
            text = "逆行が進んでいるため、まず見直し優先です。踏み上げ方向には注意です。"
    elif action_key == "SQUEEZE_WARNING":
        text = "逆行が強く、踏み上げ注意です。無理に引っぱらず警戒優先です。"
    elif action_key == "BUYBACK_CANDIDATE":
        text = "利益が大きく乗っているので、買い戻しを考えやすい場面です。"
    elif action_key == "TAKE_PROFIT_CANDIDATE":
        text = "利益が大きく乗っているので、利確を考えやすい場面です。"
    elif action_key == "TAKE_PROFIT_CAUTION":
        text = "利益は出ているので、一部利確や逆指値の見直しを考えたいです。"
    elif action_key == "REVERSAL_WARNING":
        text = "流れが反対側へ傾き気味です。すぐ断定せず、反転確認を優先したいです。"
    elif action_key == "MARGIN_STALE":
        text = "信用保有が長めです。勝っていても負けていても、長期化リスクを確認したいです。"
    elif action_key == "PRICE_MISSING":
        text = "価格が未更新なので、いったん判断保留です。"
    else:
        text = "今は大きな警戒は少なく、継続観察しやすい状態です。"

    if account == "MARGIN" and hold_days is not None and int(hold_days) >= 30 and action_key != "MARGIN_STALE":
        text += " 信用保有が長めなので、その点も確認したいです。"
    elif volatility == "大きい":
        text += " 値動きが大きいので慎重に見たいです。"
    elif flow_label == "まだ不明":
        text += " 流れがまだはっきりしないので過信は禁物です。"

    return _trim_text(text, 88)


def _one_item_block(index: int, row: dict) -> str:
    ticker = row.get("ticker") or "-"
    name = row.get("name") or ""

    lines = [
        f"{index}. {ticker} {name}".strip(),
        f"   {_direction_label(row)} / {_level_label(row)}",
        f"   現在値: {_format_price(row.get('last_price'))}",
        f"   損益率: {_format_pct(row.get('pnl_pct'))}",
    ]

    reasons = _reason_candidates(row)
    if reasons:
        lines.append(f"   根拠: {' / '.join(reasons)}")

    decision = _decision_text(row)
    if decision:
        lines.append(f"   どう見るか: {decision}")

    return "\n".join(lines)


def build_holding_notify_message(rows: Iterable[dict], max_items: int = 3) -> str:
    selected = list(rows)[:max_items]
    if not selected:
        return ""

    blocks = [_headline(selected)]
    for idx, row in enumerate(selected, start=1):
        blocks.append(_one_item_block(idx, row))

    now_str = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    blocks.append(f"時刻: {now_str}")

    return "\n\n".join(blocks)


def _trim_text(text: str, max_len: int) -> str:
    safe = (text or "").strip()
    if len(safe) <= max_len:
        return safe
    return safe[: max_len - 1] + "…"


def _chip_color(row: dict) -> tuple[str, str]:
    level = row.get("level")
    direction = row.get("direction")

    if level == "STRONG":
        return "#F43F5E", "#FFFFFF"
    if direction == "LONG":
        return "#06B6D4", "#FFFFFF"
    if direction == "SHORT":
        return "#F59E0B", "#111827"
    return "#475569", "#FFFFFF"


def _direction_emoji(row: dict) -> str:
    direction = row.get("direction")
    if direction == "LONG":
        return "📈"
    if direction == "SHORT":
        return "📉"
    return "👀"


def _make_flex_text(
    text: str,
    size: str = "sm",
    color: str = "#E5E7EB",
    weight: str = "regular",
    wrap: bool = True,
) -> dict:
    return {
        "type": "text",
        "text": text,
        "size": size,
        "color": color,
        "weight": weight,
        "wrap": wrap,
    }


def _make_badge(text: str, bg_color: str, fg_color: str) -> dict:
    return {
        "type": "box",
        "layout": "horizontal",
        "flex": 0,
        "backgroundColor": bg_color,
        "cornerRadius": "12px",
        "paddingTop": "4px",
        "paddingBottom": "4px",
        "paddingStart": "10px",
        "paddingEnd": "10px",
        "contents": [
            {
                "type": "text",
                "text": text,
                "size": "xs",
                "color": fg_color,
                "weight": "bold",
                "wrap": False,
                "align": "center",
                "gravity": "center",
                "flex": 0,
            }
        ],
    }


def _make_section_title(text: str) -> dict:
    return _make_flex_text(text, size="xs", color="#94A3B8", weight="bold")


def _make_reason_line(text: str) -> dict:
    return {
        "type": "box",
        "layout": "baseline",
        "spacing": "sm",
        "contents": [
            _make_flex_text("・", size="sm", color="#CBD5E1", wrap=False),
            _make_flex_text(_trim_text(text, 34), size="sm", color="#E2E8F0"),
        ],
    }


def _build_bubble(row: dict) -> dict:
    chip_bg, chip_fg = _chip_color(row)

    title = f"{row.get('ticker', '-')}"
    if row.get("name"):
        title += f" {row['name']}"

    reasons = _reason_candidates(row)
    decision = _decision_text(row)

    pnl_pct = _format_pct(row.get("pnl_pct"))
    pnl_yen = _format_yen(row.get("pnl_yen"))
    hold_days = row.get("hold_days")
    hold_days_text = "-" if hold_days is None else f"{int(hold_days)}日"

    body_contents = [
        _make_flex_text(_trim_text(title, 34), size="lg", color="#FFFFFF", weight="bold"),
        {
            "type": "box",
            "layout": "horizontal",
            "margin": "md",
            "spacing": "sm",
            "contents": [
                _make_badge(f"{_direction_emoji(row)} {_direction_label(row)}", "#1E293B", "#E2E8F0"),
                _make_badge(_level_label(row), chip_bg, chip_fg),
            ],
        },
        {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "spacing": "sm",
            "contents": [
                _make_section_title("現在の状態"),
                _make_flex_text(f"現在値 {_format_price(row.get('last_price'))}", size="sm", color="#E2E8F0"),
                _make_flex_text(f"損益率 {pnl_pct}", size="sm", color="#E2E8F0"),
                _make_flex_text(f"損益額 {pnl_yen}", size="sm", color="#E2E8F0"),
                _make_flex_text(f"保有日数 {hold_days_text}", size="sm", color="#E2E8F0"),
            ],
        },
        {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "spacing": "xs",
            "contents": [
                _make_section_title("ざっくり状況"),
                _make_flex_text(
                    f"流れ {_flow_label_from_row(row)} / 値動き {_volatility_label_from_row(row)}",
                    size="sm",
                    color="#E2E8F0",
                ),
            ],
        },
    ]

    if reasons:
        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "spacing": "sm",
                "contents": [
                    _make_section_title("根拠"),
                    *[_make_reason_line(reason) for reason in reasons],
                ],
            }
        )

    if decision:
        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "spacing": "sm",
                "contents": [
                    _make_section_title("どう見るか"),
                    _make_flex_text(decision, size="sm", color="#E2E8F0"),
                ],
            }
        )

    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "18px",
            "backgroundColor": "#0F172A",
            "contents": body_contents,
        },
    }


def build_holding_notify_flex(rows: Iterable[dict], max_items: int = 3) -> dict:
    selected = list(rows)[:max_items]
    if not selected:
        return {"alt_text": "", "contents": {}}

    bubbles = [_build_bubble(row) for row in selected]
    alt_text = build_holding_notify_message(selected, max_items=max_items)

    contents = {
        "type": "carousel",
        "contents": bubbles,
    }

    return {
        "alt_text": alt_text[:400],
        "contents": contents,
    }