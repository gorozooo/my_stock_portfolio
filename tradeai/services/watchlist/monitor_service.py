# =========================================================
# [FILE] monitor_service.py
# [PATH] <project_root>/tradeai/services/watchlist/monitor_service.py
#
# このファイルは何？
# - ウォッチリスト銘柄を監視して、
#   ロング注目 / ショート注目 / 様子見 を作るサービスです。
# - GC/DC・MACD・RSI・ATR・VWAP・出来高急増・高値/安値ブレイク
#   に加えて、一目均衡表・移動平均乖離・ヒゲ・ギャップも使います。
# - 画面表示は初心者向けの言葉へ翻訳します。
# =========================================================

from __future__ import annotations

from typing import Any

from tradeai.models.watchlist import WatchlistItem
from tradeai.services.indicators.atr_service import analyze_atr
from tradeai.services.indicators.breakout_service import analyze_breakout
from tradeai.services.indicators.daily_price_service import get_daily_ohlc
from tradeai.services.indicators.gap_service import analyze_gap
from tradeai.services.indicators.ichimoku_service import analyze_ichimoku
from tradeai.services.indicators.ma_cross_service import analyze_ma_cross
from tradeai.services.indicators.ma_distance_service import analyze_ma_distance
from tradeai.services.indicators.macd_service import analyze_macd
from tradeai.services.indicators.rsi_service import analyze_rsi
from tradeai.services.indicators.volume_spike_service import analyze_volume_spike
from tradeai.services.indicators.vwap_service import analyze_vwap
from tradeai.services.indicators.wick_ratio_service import analyze_wick_ratio


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
            "vwap": None,
            "vwap_state": "UNKNOWN",
            "vwap_label": "不明",
            "volume_ratio": None,
            "volume_state": "UNKNOWN",
            "volume_label": "不明",
            "breakout_state": "UNKNOWN",
            "breakout_label": "不明",
            "range_high": None,
            "range_low": None,
            "ichimoku_state": "UNKNOWN",
            "ichimoku_label": "不明",
            "tenkan": None,
            "kijun": None,
            "span_a": None,
            "span_b": None,
            "ma_distance_state": "UNKNOWN",
            "ma_distance_label": "不明",
            "ma_value": None,
            "distance_pct": None,
            "wick_state": "UNKNOWN",
            "wick_label": "不明",
            "upper_wick_pct": None,
            "lower_wick_pct": None,
            "gap_state": "UNKNOWN",
            "gap_label": "不明",
            "gap_pct": None,
        }

    ma_info = analyze_ma_cross(ohlc["closes"], short_window=5, long_window=25)
    macd_info = analyze_macd(ohlc["closes"])
    rsi_info = analyze_rsi(ohlc["closes"], period=14)
    atr_info = analyze_atr(ohlc["highs"], ohlc["lows"], ohlc["closes"], period=14)
    vwap_info = analyze_vwap(ohlc["closes"], ohlc["volumes"], lookback=20)
    volume_info = analyze_volume_spike(ohlc["volumes"], lookback=20)
    breakout_info = analyze_breakout(ohlc["highs"], ohlc["lows"], ohlc["closes"], lookback=20)
    ichimoku_info = analyze_ichimoku(ohlc["highs"], ohlc["lows"], ohlc["closes"])
    ma_distance_info = analyze_ma_distance(ohlc["closes"], window=25)
    wick_info = analyze_wick_ratio(ohlc["opens"], ohlc["highs"], ohlc["lows"], ohlc["closes"])
    gap_info = analyze_gap(ohlc["opens"], ohlc["closes"])

    return {
        "has_technical": True,
        "last_close": ohlc["last_close"],
        **ma_info,
        **macd_info,
        **rsi_info,
        **atr_info,
        **vwap_info,
        **volume_info,
        **breakout_info,
        **ichimoku_info,
        **ma_distance_info,
        **wick_info,
        **gap_info,
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

    if tech["vwap_state"] == "CROSS_UP":
        score += 2
        reasons.append("最近の売買コスト帯を上抜けています")
    elif tech["vwap_state"] == "ABOVE_VWAP":
        score += 1
        reasons.append("価格は最近の売買コスト帯より上です")
    elif tech["vwap_state"] == "CROSS_DOWN":
        score -= 2
        reasons.append("コスト帯を下抜けていて少し弱いです")
    elif tech["vwap_state"] == "BELOW_VWAP":
        score -= 1

    if tech["breakout_state"] == "HIGH_BREAKOUT":
        score += 3
        reasons.append("直近の高値をぬけています")
    elif tech["breakout_state"] == "NEAR_HIGH":
        score += 1
        reasons.append("高値に近く、もう一段上を試しやすい位置です")
    elif tech["breakout_state"] == "LOW_BREAKDOWN":
        score -= 3
        reasons.append("安値を割っていて逆風です")

    if tech["volume_state"] == "SPIKE":
        score += 2
        reasons.append("出来高も急増していて注目が集まっています")
    elif tech["volume_state"] == "ACTIVE":
        score += 1
        reasons.append("出来高もやや増えています")
    elif tech["volume_state"] == "QUIET":
        score -= 1

    if tech["volume_state"] == "SPIKE" and tech["breakout_state"] == "HIGH_BREAKOUT":
        score += 1
        reasons.append("高値ぬけに出来高が伴っています")

    if tech["ichimoku_state"] == "ABOVE_CLOUD_BULLISH":
        score += 3
        reasons.append("大きな流れも上向きです")
    elif tech["ichimoku_state"] == "ABOVE_CLOUD":
        score += 2
        reasons.append("大きな流れは上向きです")
    elif tech["ichimoku_state"] == "BELOW_CLOUD_BEARISH":
        score -= 3
        reasons.append("大きな流れは下向きで逆風です")
    elif tech["ichimoku_state"] == "BELOW_CLOUD":
        score -= 2

    if tech["ma_distance_state"] == "STRETCHED_UP":
        score -= 2
        reasons.append("少し上がりすぎで飛びつき注意です")
    elif tech["ma_distance_state"] == "SLIGHTLY_STRETCHED_UP":
        score -= 1

    if tech["wick_state"] == "LOWER_HEAVY":
        score += 1
        reasons.append("下ヒゲが強く、押し返しの形です")
    elif tech["wick_state"] == "UPPER_HEAVY":
        score -= 1
        reasons.append("上ヒゲが強く、戻り売りに注意です")

    if tech["gap_state"] == "GAP_UP":
        score += 1
        reasons.append("寄り付きは強めでした")
    elif tech["gap_state"] == "GAP_DOWN":
        score -= 1
        reasons.append("寄り付きは弱めでした")

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

    if tech["vwap_state"] == "CROSS_DOWN":
        score += 2
        reasons.append("最近の売買コスト帯を下抜けています")
    elif tech["vwap_state"] == "BELOW_VWAP":
        score += 1
        reasons.append("価格は最近の売買コスト帯より下です")
    elif tech["vwap_state"] == "CROSS_UP":
        score -= 2
        reasons.append("コスト帯を上抜けていて逆風です")
    elif tech["vwap_state"] == "ABOVE_VWAP":
        score -= 1

    if tech["breakout_state"] == "LOW_BREAKDOWN":
        score += 3
        reasons.append("直近の安値を割っています")
    elif tech["breakout_state"] == "NEAR_LOW":
        score += 1
        reasons.append("安値に近く、もう一段下を試しやすい位置です")
    elif tech["breakout_state"] == "HIGH_BREAKOUT":
        score -= 3
        reasons.append("高値をぬけていて逆風です")

    if tech["volume_state"] == "SPIKE":
        score += 2
        reasons.append("出来高も急増していて注目が集まっています")
    elif tech["volume_state"] == "ACTIVE":
        score += 1
        reasons.append("出来高もやや増えています")
    elif tech["volume_state"] == "QUIET":
        score -= 1

    if tech["volume_state"] == "SPIKE" and tech["breakout_state"] == "LOW_BREAKDOWN":
        score += 1
        reasons.append("安値わりに出来高が伴っています")

    if tech["ichimoku_state"] == "BELOW_CLOUD_BEARISH":
        score += 3
        reasons.append("大きな流れも下向きです")
    elif tech["ichimoku_state"] == "BELOW_CLOUD":
        score += 2
        reasons.append("大きな流れは下向きです")
    elif tech["ichimoku_state"] == "ABOVE_CLOUD_BULLISH":
        score -= 3
        reasons.append("大きな流れは上向きで逆風です")
    elif tech["ichimoku_state"] == "ABOVE_CLOUD":
        score -= 2

    if tech["ma_distance_state"] == "STRETCHED_DOWN":
        score -= 2
        reasons.append("少し下がりすぎで戻りに注意です")
    elif tech["ma_distance_state"] == "SLIGHTLY_STRETCHED_DOWN":
        score -= 1

    if tech["wick_state"] == "UPPER_HEAVY":
        score += 1
        reasons.append("上ヒゲが強く、押し戻されやすい形です")
    elif tech["wick_state"] == "LOWER_HEAVY":
        score -= 1
        reasons.append("下ヒゲが強く、反発に注意です")

    if tech["gap_state"] == "GAP_DOWN":
        score += 1
        reasons.append("寄り付きは弱めでした")
    elif tech["gap_state"] == "GAP_UP":
        score -= 1
        reasons.append("寄り付きは強めで逆風です")

    return score, reasons


def _level_from_score(score: int) -> str:
    if score >= 11:
        return "STRONG"
    if score >= 6:
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

    if tech["breakout_state"] == "HIGH_BREAKOUT":
        return "LONG"
    if tech["breakout_state"] == "LOW_BREAKDOWN":
        return "SHORT"

    if tech["ma_state"] in ("GOLDEN_CROSS", "ABOVE_GC"):
        return "LONG"
    if tech["ma_state"] in ("DEAD_CROSS", "BELOW_DC"):
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


def _timing_label(rsi_label: str) -> str:
    if rsi_label == "売られすぎから持ち直し":
        return "押し目反発寄り"
    if rsi_label == "買われすぎから失速":
        return "反落寄り"
    if rsi_label == "買われすぎ":
        return "上がりすぎ注意"
    if rsi_label == "売られすぎ":
        return "下がりすぎ注意"
    if rsi_label == "買い優勢":
        return "買い優勢"
    if rsi_label == "売り優勢":
        return "売り優勢"
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


def _cost_position_label(vwap_state: str) -> str:
    if vwap_state == "CROSS_UP":
        return "コスト帯を上抜け"
    if vwap_state == "ABOVE_VWAP":
        return "コスト帯より上"
    if vwap_state == "CROSS_DOWN":
        return "コスト帯を下抜け"
    if vwap_state == "BELOW_VWAP":
        return "コスト帯より下"
    return "まだ不明"


def _volume_label(volume_label: str) -> str:
    if volume_label == "出来高急増":
        return "かなり活発"
    if volume_label == "やや活発":
        return "やや活発"
    if volume_label == "かなり静か":
        return "かなり静か"
    if volume_label == "ふつう":
        return "ふつう"
    return "まだ不明"


def _breakout_label(breakout_label: str) -> str:
    if breakout_label == "高値ぬけ":
        return "高値ぬけ"
    if breakout_label == "安値わり":
        return "安値わり"
    if breakout_label == "高値に近い":
        return "高値に近い"
    if breakout_label == "安値に近い":
        return "安値に近い"
    if breakout_label == "まだ節目内":
        return "まだ節目内"
    return "まだ不明"


def _big_flow_label(ichimoku_label: str) -> str:
    if ichimoku_label == "雲の上で上向き":
        return "大きな流れも上向き"
    if ichimoku_label == "雲の上":
        return "大きな流れは上向き"
    if ichimoku_label == "雲の下で下向き":
        return "大きな流れも下向き"
    if ichimoku_label == "雲の下":
        return "大きな流れは下向き"
    if ichimoku_label == "雲の中":
        return "大きな流れははっきりしない"
    return "まだ不明"


def _distance_human_label(ma_distance_label: str) -> str:
    if ma_distance_label == "かなり上がりすぎ":
        return "かなり上がりすぎ"
    if ma_distance_label == "やや上がりすぎ":
        return "やや上がりすぎ"
    if ma_distance_label == "かなり下がりすぎ":
        return "かなり下がりすぎ"
    if ma_distance_label == "やや下がりすぎ":
        return "やや下がりすぎ"
    if ma_distance_label == "離れすぎではない":
        return "行きすぎなし"
    return "まだ不明"


def _wick_human_label(wick_label: str) -> str:
    if wick_label == "上ヒゲ強め":
        return "上ヒゲ強め"
    if wick_label == "下ヒゲ強め":
        return "下ヒゲ強め"
    if wick_label == "ヒゲはふつう":
        return "ヒゲふつう"
    return "まだ不明"


def _gap_human_label(gap_label: str) -> str:
    if gap_label == "ギャップアップ":
        return "ギャップアップ"
    if gap_label == "ギャップダウン":
        return "ギャップダウン"
    if gap_label == "大きなギャップなし":
        return "大きなギャップなし"
    return "まだ不明"


def _summary_for(direction: str | None, level: str, reasons: list[str], atr_pct: float | None) -> str:
    if direction is None or level == "REFERENCE":
        text = "今は強い重なりが少ないので、まだ様子見しやすい状態です。"
    elif direction == "LONG":
        if level == "STRONG":
            text = "上向きの流れ・勢い・節目ぬけが重なっています。ロング側でかなり注目したい状態です。"
        else:
            text = "ややロング寄りです。上向きシグナルが少しずつ重なっています。"
    else:
        if level == "STRONG":
            text = "下向きの流れ・勢い・節目わりが重なっています。ショート側でかなり注目したい状態です。"
        else:
            text = "ややショート寄りです。下向きシグナルが少しずつ重なっています。"

    if reasons:
        text += " " + " / ".join(reasons[:4]) + "。"

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
        "timing_label": _timing_label(tech["rsi_label"]),
        "volatility_label": _volatility_label(tech["atr_pct"]),
        "cost_position_label": _cost_position_label(tech["vwap_state"]),
        "volume_human_label": _volume_label(tech["volume_label"]),
        "breakout_human_label": _breakout_label(tech["breakout_label"]),
        "big_flow_label": _big_flow_label(tech["ichimoku_label"]),
        "distance_human_label": _distance_human_label(tech["ma_distance_label"]),
        "wick_human_label": _wick_human_label(tech["wick_label"]),
        "gap_human_label": _gap_human_label(tech["gap_label"]),
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
        "vwap": tech["vwap"],
        "vwap_state": tech["vwap_state"],
        "vwap_label": tech["vwap_label"],
        "volume_ratio": tech["volume_ratio"],
        "volume_state": tech["volume_state"],
        "volume_label": tech["volume_label"],
        "breakout_state": tech["breakout_state"],
        "breakout_label": tech["breakout_label"],
        "range_high": tech["range_high"],
        "range_low": tech["range_low"],
        "ichimoku_state": tech["ichimoku_state"],
        "ichimoku_label": tech["ichimoku_label"],
        "tenkan": tech["tenkan"],
        "kijun": tech["kijun"],
        "span_a": tech["span_a"],
        "span_b": tech["span_b"],
        "ma_distance_state": tech["ma_distance_state"],
        "ma_distance_label": tech["ma_distance_label"],
        "ma_value": tech["ma_value"],
        "distance_pct": tech["distance_pct"],
        "wick_state": tech["wick_state"],
        "wick_label": tech["wick_label"],
        "upper_wick_pct": tech["upper_wick_pct"],
        "lower_wick_pct": tech["lower_wick_pct"],
        "gap_state": tech["gap_state"],
        "gap_label": tech["gap_label"],
        "gap_pct": tech["gap_pct"],
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