# =========================================================
# [FILE] monitor_service.py
# [PATH] <project_root>/tradeai/services/watchlist/monitor_service.py
#
# このファイルは何？
# - ウォッチリスト銘柄を監視して、
#   ロング注目 / ショート注目 / 様子見 を作るサービスです。
# - GC/DC・MACD・RSI・ATR を使いますが、
#   画面表示は初心者向けの言葉へ翻訳します。
# =========================================================

from __future__ import annotations

from typing import Any

from tradeai.models.watchlist import WatchlistItem
from tradeai.services.indicators.atr_service import analyze_atr
from tradeai.services.indicators.daily_price_service import get_daily_ohlc
from tradeai.services.indicators.ma_cross_service import analyze_ma_cross
from tradeai.services.indicators.macd_service import analyze_macd
from tradeai.services.indicators.rsi_service import analyze_rsi


LEVEL_RANK = {
    "STRONG": 0,
    "ATTENTION": 1,
    "REFERENCE": 2,
}


def _build_technical_snapshot(ticker: str) -> dict[str, Any]:
    ohlc = get_daily_ohlc(ticker)
    if not ohlc:
        return {
            "has_technical": False,
            "last_close": None,
            "ma_state": "UNKNOWN",
            "ma_label": "不明",
            "macd_state": "UNKNOWN",
            "macd_label": "不明",
            "rsi_state": "UNKNOWN",
            "rsi_label": "不明",
            "rsi": None,
            "atr": None,
            "atr_pct": None,
        }

    ma_info = analyze_ma_cross(ohlc["closes"], short_window=5, long_window=25)
    macd_info = analyze_macd(ohlc["closes"])
    rsi_info = analyze_rsi(ohlc["closes"], period=14)
    atr_info = analyze_atr(ohlc["highs"], ohlc["lows"], ohlc["closes"], period=14)

    return {
        "has_technical": True,
        "last_close": ohlc["last_close"],
        **ma_info,
        **macd_info,
        **rsi_info,
        **atr_info,
    }


def _score_long(tech: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if tech["ma_state"] == "GOLDEN_CROSS":
        score += 3
        reasons.append("流れが上向きに変わり始めています")
    elif tech["ma_state"] == "ABOVE_GC":
        score += 2
        reasons.append("流れは上向きです")

    if tech["macd_state"] == "BULLISH_CROSS":
        score += 3
        reasons.append("勢いも上向きに変わり始めています")
    elif tech["macd_state"] == "BULLISH_ABOVE":
        score += 2
        reasons.append("勢いは上向きです")

    if tech["rsi_state"] == "REBOUND_UP":
        score += 2
        reasons.append("売られすぎから持ち直し気味です")
    elif tech["rsi_state"] == "BULLISH":
        score += 1
        reasons.append("買いがやや優勢です")
    elif tech["rsi_state"] == "OVERBOUGHT":
        score -= 1
        reasons.append("少し上がりすぎには注意です")

    return score, reasons


def _score_short(tech: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if tech["ma_state"] == "DEAD_CROSS":
        score += 3
        reasons.append("流れが下向きに変わり始めています")
    elif tech["ma_state"] == "BELOW_DC":
        score += 2
        reasons.append("流れは下向きです")

    if tech["macd_state"] == "BEARISH_CROSS":
        score += 3
        reasons.append("勢いも下向きに変わり始めています")
    elif tech["macd_state"] == "BEARISH_BELOW":
        score += 2
        reasons.append("勢いは下向きです")

    if tech["rsi_state"] == "ROLLOVER_DOWN":
        score += 2
        reasons.append("買われすぎから失速気味です")
    elif tech["rsi_state"] == "BEARISH":
        score += 1
        reasons.append("売りがやや優勢です")
    elif tech["rsi_state"] == "OVERSOLD":
        score -= 1
        reasons.append("少し下がりすぎには注意です")

    return score, reasons


def _level_from_score(score: int) -> str:
    if score >= 7:
        return "STRONG"
    if score >= 4:
        return "ATTENTION"
    return "REFERENCE"


def _pick_direction(item: WatchlistItem, long_score: int, short_score: int, tech: dict[str, Any]) -> str | None:
    enabled_long = bool(item.long_enabled)
    enabled_short = bool(item.short_enabled)

    if enabled_long and not enabled_short:
        return "LONG"
    if enabled_short and not enabled_long:
        return "SHORT"

    if long_score > short_score:
        return "LONG"
    if short_score > long_score:
        return "SHORT"

    if tech["ma_state"] in ("GOLDEN_CROSS", "ABOVE_GC"):
        return "LONG"
    if tech["ma_state"] in ("DEAD_CROSS", "BELOW_DC"):
        return "SHORT"

    if tech["macd_state"] in ("BULLISH_CROSS", "BULLISH_ABOVE"):
        return "LONG"
    if tech["macd_state"] in ("BEARISH_CROSS", "BEARISH_BELOW"):
        return "SHORT"

    return None


def _flow_label(ma_state: str) -> str:
    if ma_state == "GOLDEN_CROSS":
        return "上向きに転換"
    if ma_state == "ABOVE_GC":
        return "上向き"
    if ma_state == "DEAD_CROSS":
        return "下向きに転換"
    if ma_state == "BELOW_DC":
        return "下向き"
    return "まだ不明"


def _momentum_label(macd_state: str) -> str:
    if macd_state == "BULLISH_CROSS":
        return "上向きに転換"
    if macd_state == "BULLISH_ABOVE":
        return "上向き"
    if macd_state == "BEARISH_CROSS":
        return "下向きに転換"
    if macd_state == "BEARISH_BELOW":
        return "下向き"
    return "まだ不明"


def _volatility_label(atr_pct: float | None) -> str:
    if atr_pct is None:
        return "まだ不明"
    if atr_pct >= 4:
        return "大きい"
    if atr_pct >= 2:
        return "やや大きめ"
    if atr_pct >= 1:
        return "ふつう"
    return "小さめ"


def _summary_for(direction: str | None, level: str, reasons: list[str], atr_pct: float | None) -> str:
    if direction is None or level == "REFERENCE":
        text = "今は強い重なりが少ないので、まだ様子見しやすい状態です。"
    elif direction == "LONG":
        if level == "STRONG":
            text = "上向きの流れと勢いが重なっています。ロング側でかなり注目したい状態です。"
        else:
            text = "ややロング寄りです。上向きシグナルが少しずつ重なっています。"
    else:
        if level == "STRONG":
            text = "下向きの流れと勢いが重なっています。ショート側でかなり注目したい状態です。"
        else:
            text = "ややショート寄りです。下向きシグナルが少しずつ重なっています。"

    if reasons:
        text += " " + " / ".join(reasons[:2]) + "。"

    if atr_pct is not None:
        if atr_pct >= 4:
            text += " 値動きは大きめです。"
        elif atr_pct >= 2:
            text += " 値動きはやや大きめです。"
        elif atr_pct < 1:
            text += " 値動きは小さめです。"

    return text.strip()


def build_watch_signal_row(item: WatchlistItem) -> dict[str, Any]:
    tech = _build_technical_snapshot(item.ticker)

    long_score, long_reasons = _score_long(tech)
    short_score, short_reasons = _score_short(tech)

    chosen_direction = _pick_direction(item, long_score, short_score, tech)

    if chosen_direction == "LONG":
        selected_score = long_score
        selected_reasons = long_reasons
    elif chosen_direction == "SHORT":
        selected_score = short_score
        selected_reasons = short_reasons
    else:
        selected_score = max(long_score, short_score)
        selected_reasons = []

    level = _level_from_score(selected_score)

    if chosen_direction == "LONG":
        action_label = "ロング注目" if level == "STRONG" else "ロング候補" if level == "ATTENTION" else "様子見"
    elif chosen_direction == "SHORT":
        action_label = "ショート注目" if level == "STRONG" else "ショート候補" if level == "ATTENTION" else "様子見"
    else:
        action_label = "様子見"

    summary_text = _summary_for(
        direction=chosen_direction,
        level=level,
        reasons=selected_reasons,
        atr_pct=tech["atr_pct"],
    )

    emit_event = chosen_direction is not None and level in ("ATTENTION", "STRONG")

    return {
        "watchlist_item": item,
        "ticker": item.ticker,
        "name": item.name or "",
        "long_enabled": item.long_enabled,
        "short_enabled": item.short_enabled,
        "notify_enabled": item.notify_enabled,
        "priority": item.priority,
        "chosen_direction": chosen_direction,
        "level": level,
        "action_label": action_label,
        "emit_event": emit_event and item.notify_enabled and item.is_active,
        "long_score": long_score,
        "short_score": short_score,
        "flow_label": _flow_label(tech["ma_state"]),
        "momentum_label": _momentum_label(tech["macd_state"]),
        "volatility_label": _volatility_label(tech["atr_pct"]),
        "summary_text": summary_text,
        "last_close": tech["last_close"],
        "ma_state": tech["ma_state"],
        "ma_label": tech["ma_label"],
        "macd_state": tech["macd_state"],
        "macd_label": tech["macd_label"],
        "rsi_state": tech["rsi_state"],
        "rsi_label": tech["rsi_label"],
        "rsi": tech["rsi"],
        "atr": tech["atr"],
        "atr_pct": tech["atr_pct"],
        "selected_reasons": selected_reasons,
    }


def build_watch_signal_rows(user) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    items = (
        WatchlistItem.objects.filter(user=user, is_active=True)
        .order_by("priority", "ticker")
    )

    for item in items:
        rows.append(build_watch_signal_row(item))

    rows.sort(
        key=lambda row: (
            LEVEL_RANK.get(row["level"], 9),
            -(max(row["long_score"], row["short_score"])),
            row["priority"],
            row["ticker"],
        )
    )
    return rows