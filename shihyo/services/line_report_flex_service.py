"""
[FILE] line_report_flex_service.py
[PATH] <project_root>/shihyo/services/line_report_flex_service.py

このファイルは何？
- line_report_data_service が作った report context から、
  LINE Flex Message の altText と contents を組み立てるサービスです。
- 見た目専用です。DB検索や LINE送信はしません。

今回の修正ポイント：
- AI予想ブロックから「基準値」の表示を削除
- 主要4指標はそのまま維持
- 市場の偏りは行形式のまま維持
"""

from __future__ import annotations

from typing import Any


def _format_price(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _format_signed_number(value: float | None, digits: int = 2) -> str:
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


def _join_items(items: list[str]) -> str:
    return " / ".join(items) if items else "-"


def _text(
    text: str,
    size: str = "sm",
    color: str = "#FFFFFF",
    weight: str = "regular",
    wrap: bool = True,
    align: str | None = None,
    margin: str | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "type": "text",
        "text": text,
        "size": size,
        "color": color,
        "weight": weight,
        "wrap": wrap,
    }
    if align:
        obj["align"] = align
    if margin:
        obj["margin"] = margin
    return obj


def _separator(margin: str = "md") -> dict[str, Any]:
    return {
        "type": "separator",
        "margin": margin,
        "color": "#2A3143",
    }


def _pill(text: str, bg: str, color: str = "#FFFFFF") -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "horizontal",
        "paddingStart": "10px",
        "paddingEnd": "10px",
        "paddingTop": "5px",
        "paddingBottom": "5px",
        "cornerRadius": "12px",
        "backgroundColor": bg,
        "contents": [
            _text(text, size="xs", color=color, weight="bold", wrap=False),
        ],
    }


def _section_title(left: str, right: dict[str, Any] | None = None) -> dict[str, Any]:
    if right:
        return {
            "type": "box",
            "layout": "horizontal",
            "justifyContent": "space-between",
            "alignItems": "center",
            "contents": [
                _text(left, size="md", color="#FFFFFF", weight="bold"),
                right,
            ],
        }
    return {
        "type": "box",
        "layout": "horizontal",
        "contents": [
            _text(left, size="md", color="#FFFFFF", weight="bold"),
        ],
    }


def _kv_row(label: str, value: str) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "baseline",
        "spacing": "sm",
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": "xs",
                "color": "#AEB7CC",
                "flex": 3,
                "wrap": False,
            },
            {
                "type": "text",
                "text": value,
                "size": "xs",
                "color": "#FFFFFF",
                "weight": "bold",
                "flex": 7,
                "wrap": True,
                "align": "end",
            },
        ],
    }


def _stat_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#151C2D",
        "cornerRadius": "14px",
        "paddingAll": "10px",
        "flex": 1,
        "contents": [
            _text(card["title"], size="xs", color="#AEB7CC"),
            _text(_format_price(card["value"], card["digits"]), size="lg", color="#FFFFFF", weight="bold", margin="sm"),
            _text(
                f"{_format_signed_number(card['change'], card['digits'])} / {_format_signed_percent(card['change_pct'], 2)}",
                size="sm",
                color="#D8DEE9",
                weight="bold",
                margin="sm",
            ),
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "md",
                "backgroundColor": card["badge_bg"],
                "cornerRadius": "10px",
                "paddingTop": "5px",
                "paddingBottom": "5px",
                "paddingStart": "8px",
                "paddingEnd": "8px",
                "contents": [
                    _text(card["badge_text"], size="xs", color="#FFFFFF", weight="bold", align="center", wrap=False),
                ],
            },
        ],
    }


def build_alt_text(report: dict[str, Any]) -> str:
    generated_at = report["generated_at"]
    market_cards = report["market_cards"]
    ai = report["ai"]
    bias = report["bias"]

    parts: list[str] = [
        f"指標レポート {generated_at.strftime('%Y-%m-%d %H:%M')}",
    ]

    if market_cards:
        nikkei = market_cards[0]
        parts.append(
            f"日経平均 {_format_price(nikkei['value'], nikkei['digits'])} "
            f"({_format_signed_number(nikkei['change'], nikkei['digits'])} / {_format_signed_percent(nikkei['change_pct'], 2)})"
        )

    if ai.get("available"):
        parts.append(
            f"{ai['slot_label']} {ai['label']} "
            f"{_format_price(ai['pred_close_value'], 2)} "
            f"({_format_signed_percent(ai['pred_close_pct'], 2)})"
        )

    if bias.get("available"):
        parts.append(f"{bias['mode_label']} {bias['tone_label']}")

    return " / ".join(parts)


def build_flex_contents(report: dict[str, Any]) -> dict[str, Any]:
    generated_at = report["generated_at"]
    market_cards = report["market_cards"]
    risk = report["risk"]
    ai = report["ai"]
    bias = report["bias"]

    body_contents: list[dict[str, Any]] = []

    body_contents.extend([
        {
            "type": "box",
            "layout": "vertical",
            "contents": [
                _text("📡 指標レポート", size="xl", color="#FFFFFF", weight="bold"),
                _text(generated_at.strftime("%Y-%m-%d %H:%M"), size="xs", color="#AEB7CC", margin="sm"),
            ],
        },
        _separator("lg"),
    ])

    # 主要4指標
    body_contents.extend([
        _section_title("主要4指標"),
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "margin": "md",
            "contents": [
                _stat_card(market_cards[0]),
                _stat_card(market_cards[1]),
            ],
        },
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "margin": "sm",
            "contents": [
                _stat_card(market_cards[2]),
                _stat_card(market_cards[3]),
            ],
        },
        {
            "type": "box",
            "layout": "vertical",
            "margin": "md",
            "backgroundColor": "#151C2D",
            "cornerRadius": "14px",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "justifyContent": "space-between",
                    "alignItems": "center",
                    "contents": [
                        _text("リスク", size="sm", color="#AEB7CC", weight="bold"),
                        _pill(risk["title"], risk["badge_bg"]),
                    ],
                },
                _text(risk["memo"], size="sm", color="#FFFFFF", margin="md"),
            ],
        },
        _separator("lg"),
    ])

    # AI予想
    body_contents.append(_section_title("AI予想"))

    if not ai.get("available"):
        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#151C2D",
                "cornerRadius": "14px",
                "paddingAll": "12px",
                "contents": [
                    _text("予想データがまだありません", size="sm", color="#FFFFFF"),
                ],
            }
        )
    else:
        header_right = {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                _pill(ai["slot_label"], "#2B3348"),
                _pill(ai["label"], ai["badge_bg"]),
            ],
        }

        ai_contents: list[dict[str, Any]] = [
            {
                "type": "box",
                "layout": "horizontal",
                "justifyContent": "space-between",
                "alignItems": "center",
                "contents": [
                    _text("予想サマリー", size="xs", color="#AEB7CC", weight="bold"),
                    header_right,
                ],
            },
            {
                "type": "box",
                "layout": "horizontal",
                "justifyContent": "space-between",
                "alignItems": "center",
                "margin": "md",
                "contents": [
                    _text("確信度", size="xs", color="#AEB7CC"),
                    _text(
                        f"{round(ai['confidence'])}%" if ai.get("confidence") is not None else "-",
                        size="md",
                        color="#FFFFFF",
                        weight="bold",
                    ),
                ],
            },
        ]

        if ai.get("has_result"):
            ai_contents.extend([
                _separator("md"),
                {
                    "type": "box",
                    "layout": "horizontal",
                    "justifyContent": "space-between",
                    "alignItems": "center",
                    "margin": "md",
                    "contents": [
                        _text("結果", size="xs", color="#AEB7CC", weight="bold"),
                        _pill(ai["result_label"], ai["result_bg"]),
                    ],
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "md",
                    "spacing": "sm",
                    "contents": [
                        _kv_row("予想値", f"{_format_price(ai['pred_close_value'], 2)} ({_format_signed_percent(ai['pred_close_pct'], 2)})"),
                        _kv_row(
                            "実績値",
                            f"{_format_price(ai['actual_close_value'], 2)} ({_format_signed_number(ai['actual_delta_abs'], 2)} / {_format_signed_percent(ai['actual_close_pct'], 2)})",
                        ),
                        _kv_row("誤差", f"{ai['error_pct']:.2f}pt" if ai.get("error_pct") is not None else "-"),
                    ],
                },
            ])
        else:
            ai_contents.extend([
                _separator("md"),
                _text("予想値", size="xs", color="#AEB7CC", weight="bold", margin="md"),
                _text(_format_price(ai["pred_close_value"], 2), size="xxl", color="#FFFFFF", weight="bold", margin="sm"),
                _text(
                    f"{_format_signed_number(ai['pred_delta_abs'], 2)} / {_format_signed_percent(ai['pred_close_pct'], 2)}",
                    size="md",
                    color="#D8DEE9",
                    weight="bold",
                    margin="sm",
                ),
                _text("理由", size="xs", color="#AEB7CC", weight="bold", margin="md"),
            ])

            for reason in ai.get("reasons", [])[:3]:
                ai_contents.append(_text(f"・{reason}", size="sm", color="#FFFFFF", margin="sm"))

        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#151C2D",
                "cornerRadius": "14px",
                "paddingAll": "12px",
                "contents": ai_contents,
            }
        )

    body_contents.append(_separator("lg"))

    # 市場の偏り
    body_contents.append(_section_title("市場の偏り"))

    if not bias.get("available"):
        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#151C2D",
                "cornerRadius": "14px",
                "paddingAll": "12px",
                "contents": [
                    _text("偏りデータがまだありません", size="sm", color="#FFFFFF"),
                ],
            }
        )
    else:
        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#151C2D",
                "cornerRadius": "14px",
                "paddingAll": "12px",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "justifyContent": "space-between",
                        "alignItems": "center",
                        "contents": [
                            _pill(bias["mode_label"], "#2B3348"),
                            _pill(bias["tone_label"], bias["tone_bg"]),
                        ],
                    },
                    _text(bias["summary_text"], size="sm", color="#FFFFFF", margin="md"),
                    _separator("md"),
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "md",
                        "spacing": "sm",
                        "contents": [
                            _kv_row("強い業種", _join_items(bias["strong"])),
                            _kv_row("弱い業種", _join_items(bias["weak"])),
                            _kv_row("値上がり偏り", _join_items(bias["hot"])),
                            _kv_row("値下がり偏り", _join_items(bias["cold"])),
                        ],
                    },
                ],
            }
        )

    body_contents.append(
        _text("※ ダッシュボードと同じ基準で作成", size="xs", color="#7F8AA3", margin="lg", align="center")
    )

    return {
        "type": "bubble",
        "size": "giga",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "backgroundColor": "#0B1020",
            "contents": body_contents,
        },
    }