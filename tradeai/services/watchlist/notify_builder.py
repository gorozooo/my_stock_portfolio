# =========================================================
# [FILE] notify_builder.py
# [PATH] <project_root>/tradeai/services/watchlist/notify_builder.py
#
# このファイルは何？
# - ウォッチ監視の結果を、初心者向けのLINE通知文に変換するサービスです。
# - 1件ずつではなく、上位数件を1通にまとめる前提です。
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

    summary = (row.get("summary_text") or "").strip()
    if len(summary) > 110:
        summary = summary[:110] + "…"

    lines = [
        f"{index}. {ticker} {name}".strip(),
        f"   {_direction_label(row)} / {_level_label(row)}",
        f"   流れ: {row.get('flow_label', 'まだ不明')} / 節目: {row.get('breakout_human_label', 'まだ不明')}",
        f"   現在値: {price_text}",
        f"   {summary}",
    ]
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