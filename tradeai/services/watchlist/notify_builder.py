# =========================================================
# [FILE] notify_builder.py
# [PATH] <project_root>/tradeai/services/watchlist/notify_builder.py
#
# このファイルは何？
# - ウォッチ監視の結果を、LINE通知用の見やすい形へ整えるサービスです。
# - テキスト通知の文面だけでなく、Flex Message のカードも作ります。
# - 今回は「根拠」と「どう見るか」を分けて、縦読みしやすい構成にしています。
# =========================================================

from __future__ import annotations

from typing import Iterable

from django.utils import timezone


def build_watch_notify_dedupe_key(user_id: int, row: dict) -> str:
    ticker = row.get("ticker") or ""
    direction = row.get("chosen_direction") or "NONE"
    action = row.get("action_label") or ""
    flow = row.get("flow_label") or ""
    breakout = row.get("breakout_human_label") or ""
    return f"watch:{user_id}:{ticker}:{direction}:{action}:{flow}:{breakout}"


def _headline(rows: list[dict]) -> str:
    strong_count = sum(1 for row in rows if row.get("level") == "STRONG")
    attention_count = sum(1 for row in rows if row.get("level") == "ATTENTION")

    if strong_count > 0:
        return f"TradeAI ウォッチ通知\n強い注目 {strong_count}件 / 注目 {attention_count}件"
    return f"TradeAI ウォッチ通知\n注目 {attention_count}件"


def _direction_label(row: dict) -> str:
    direction = row.get("chosen_direction")
    if direction == "LONG":
        return "ロング寄り"
    if direction == "SHORT":
        return "ショート寄り"
    return "様子見"


def _level_label(row: dict) -> str:
    level = row.get("level")
    if level == "STRONG":
        return "かなり注目"
    if level == "ATTENTION":
        return "注目"
    return "参考"


def _one_item_block(index: int, row: dict) -> str:
    ticker = row.get("ticker") or "-"
    name = row.get("name") or ""
    last_close = row.get("last_close")
    price_text = f"{last_close:.2f}" if isinstance(last_close, (int, float)) else "-"

    reasons = _reasons_for_card(row)
    decision = _decision_text(row)

    lines = [
        f"{index}. {ticker} {name}".strip(),
        f"   {_direction_label(row)} / {_level_label(row)}",
        f"   現在値: {price_text}",
    ]

    if reasons:
        lines.append(f"   根拠: {' / '.join(reasons)}")

    if decision:
        lines.append(f"   どう見るか: {decision}")

    return "\n".join(lines)


def build_watch_notify_message(rows: Iterable[dict], max_items: int = 3) -> str:
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
    direction = row.get("chosen_direction")

    if level == "STRONG":
        return "#F43F5E", "#FFFFFF"
    if direction == "LONG":
        return "#06B6D4", "#FFFFFF"
    if direction == "SHORT":
        return "#F59E0B", "#111827"
    return "#475569", "#FFFFFF"


def _direction_emoji(row: dict) -> str:
    direction = row.get("chosen_direction")
    if direction == "LONG":
        return "📈"
    if direction == "SHORT":
        return "📉"
    return "👀"


def _price_text(row: dict) -> str:
    last_close = row.get("last_close")
    if isinstance(last_close, (int, float)):
        return f"{last_close:.2f}"
    return "-"


def _reasons_for_card(row: dict) -> list[str]:
    reasons = list(row.get("selected_reasons") or [])
    cleaned = [str(r).strip() for r in reasons if str(r).strip()]
    return cleaned[:3]


def _decision_text(row: dict) -> str:
    direction = row.get("chosen_direction")
    level = row.get("level")
    breakout = row.get("breakout_human_label") or ""
    distance = row.get("distance_human_label") or ""
    volatility = row.get("volatility_label") or ""
    big_flow = row.get("big_flow_label") or ""

    if direction == "LONG":
        if level == "STRONG":
            text = "上方向の強さ確認向きです。押し目待ちより継続の強さを見たい場面です。"
        elif breakout in ("高値ぬけ", "高値に近い"):
            text = "上抜け待ちで見やすい場面です。飛びつきすぎには注意です。"
        else:
            text = "上向き寄りですが、まだ無理に追わず確認優先です。"
    elif direction == "SHORT":
        if level == "STRONG":
            text = "下方向の継続確認向きです。戻り待ちか安値更新を見たい場面です。"
        elif breakout in ("安値わり", "安値に近い"):
            text = "下抜け確認で見やすい場面です。売り急ぎには注意です。"
        else:
            text = "下向き寄りですが、反発を挟む可能性にも注意です。"
    else:
        text = "強い重なりはまだ少なく、今は様子見寄りです。"

    if distance in ("かなり上がりすぎ", "かなり下がりすぎ"):
        text += " 行きすぎ感もあるので、追いかけすぎは注意です。"
    elif volatility == "大きい":
        text += " 値動きが大きいので、無理に入らず慎重に見たいです。"
    elif "はっきりしない" in big_flow:
        text += " 大きな流れが曖昧なので過信は禁物です。"

    return _trim_text(text, 84)


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

    reasons = _reasons_for_card(row)
    decision = _decision_text(row)

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
            "spacing": "xs",
            "contents": [
                _make_section_title("現在値"),
                _make_flex_text(_price_text(row), size="xl", color="#FFFFFF", weight="bold"),
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
                    f"流れ {row.get('flow_label', 'まだ不明')} / 節目 {row.get('breakout_human_label', 'まだ不明')}",
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


def build_watch_notify_flex(rows: Iterable[dict], max_items: int = 3) -> dict:
    selected = list(rows)[:max_items]
    if not selected:
        return {"alt_text": "", "contents": {}}

    bubbles = [_build_bubble(row) for row in selected]
    alt_text = build_watch_notify_message(selected, max_items=max_items)

    contents = {
        "type": "carousel",
        "contents": bubbles,
    }

    return {
        "alt_text": alt_text[:400],
        "contents": contents,
    }