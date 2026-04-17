# =========================================================
# [FILE] candidate_service.py
# [PATH] <project_root>/tradeai/services/candidates/candidate_service.py
#
# このファイルは何？
# - ユニバース全体から、ロング候補 / ショート候補を抽出するサービスです。
# - 既存の監視ロジックと同じ指標群を使いながら、
#   地合い補正を加えて候補を並べます。
# - できるだけ重くなりすぎないよう、yfinance はまとめて取得します。
# =========================================================

from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

from tradeai.models.regime_snapshot import RegimeSnapshot
from tradeai.models.universe import UniverseTicker
from tradeai.services.indicators.atr_service import analyze_atr
from tradeai.services.indicators.breakout_service import analyze_breakout
from tradeai.services.indicators.gap_service import analyze_gap
from tradeai.services.indicators.ichimoku_service import analyze_ichimoku
from tradeai.services.indicators.ma_cross_service import analyze_ma_cross
from tradeai.services.indicators.ma_distance_service import analyze_ma_distance
from tradeai.services.indicators.macd_service import analyze_macd
from tradeai.services.indicators.rsi_service import analyze_rsi
from tradeai.services.indicators.volume_spike_service import analyze_volume_spike
from tradeai.services.indicators.vwap_service import analyze_vwap
from tradeai.services.indicators.wick_ratio_service import analyze_wick_ratio
from tradeai.services.regime.regime_service import ensure_recent_regime_snapshot
from tradeai.services.watchlist.monitor_service import (
    _big_flow_label,
    _breakout_label,
    _cost_position_label,
    _distance_human_label,
    _flow_label,
    _gap_human_label,
    _score_long,
    _score_short,
    _timing_label,
    _momentum_label,
    _volatility_label,
    _volume_label,
    _wick_human_label,
)


LEVEL_RANK = {
    "STRONG": 0,
    "ATTENTION": 1,
    "REFERENCE": 2,
}


def _to_symbol(raw_ticker: str) -> str:
    value = (raw_ticker or "").strip().upper()
    if value.endswith(".T"):
        return value
    if value.isdigit():
        return f"{value}.T"
    return value


def _safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _trim_text(text: str, max_len: int) -> str:
    safe = (text or "").strip()
    if len(safe) <= max_len:
        return safe
    return safe[: max_len - 1] + "…"


def _batch_download_ohlc(items: list[UniverseTicker], period: str = "6mo") -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not items:
        return result

    symbol_map = {item.ticker: _to_symbol(item.ticker) for item in items}
    symbols = list(dict.fromkeys(symbol_map.values()))
    if not symbols:
        return result

    try:
        data = yf.download(
            symbols,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return result

    if data is None or data.empty:
        return result

    for item in items:
        raw_ticker = item.ticker
        symbol = symbol_map.get(raw_ticker)
        if not symbol:
            continue

        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.get_level_values(0):
                    continue
                part = data[symbol].copy()
            else:
                part = data.copy()

            if part is None or part.empty:
                continue

            part = part.dropna(subset=["Open", "High", "Low", "Close"])
            if part.empty:
                continue

            opens = [float(v) for v in part["Open"].fillna(0).tolist()]
            highs = [float(v) for v in part["High"].fillna(0).tolist()]
            lows = [float(v) for v in part["Low"].fillna(0).tolist()]
            closes = [float(v) for v in part["Close"].fillna(0).tolist()]
            volumes = [float(v) for v in part["Volume"].fillna(0).tolist()] if "Volume" in part.columns else [0.0] * len(part)

            result[raw_ticker] = {
                "symbol": symbol,
                "opens": opens,
                "highs": highs,
                "lows": lows,
                "closes": closes,
                "volumes": volumes,
                "last_open": opens[-1] if opens else None,
                "last_close": closes[-1] if closes else None,
                "last_high": highs[-1] if highs else None,
                "last_low": lows[-1] if lows else None,
                "last_volume": volumes[-1] if volumes else None,
            }
        except Exception:
            continue

    return result


def _build_technical_snapshot_from_ohlc(ohlc: dict[str, Any] | None) -> dict[str, Any]:
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


def _pick_direction(long_score: int, short_score: int, tech: dict[str, Any]) -> str | None:
    if long_score > short_score:
        return "LONG"
    if short_score > long_score:
        return "SHORT"

    if tech.get("breakout_state") == "HIGH_BREAKOUT":
        return "LONG"
    if tech.get("breakout_state") == "LOW_BREAKDOWN":
        return "SHORT"

    if tech.get("ma_state") in ("GOLDEN_CROSS", "ABOVE_GC"):
        return "LONG"
    if tech.get("ma_state") in ("DEAD_CROSS", "BELOW_DC"):
        return "SHORT"

    return None


def _level_from_score(score: int) -> str:
    if score >= 13:
        return "STRONG"
    if score >= 9:
        return "ATTENTION"
    return "REFERENCE"


def _source_labels(item: UniverseTicker) -> list[str]:
    labels: list[str] = []
    if item.from_holding:
        labels.append("保有")
    if item.from_watchlist:
        labels.append("ウォッチ")
    if item.in_nikkei225:
        labels.append("日経225")
    if item.in_topix:
        labels.append("TOPIX")
    return labels


def _source_rank(item: UniverseTicker) -> int:
    if item.from_holding:
        return 0
    if item.from_watchlist:
        return 1
    if item.in_nikkei225:
        return 2
    if item.in_topix:
        return 3
    return 4


def _regime_adjustments(snapshot: RegimeSnapshot | None) -> tuple[int, int]:
    if not snapshot:
        return 0, 0

    if snapshot.market_bias == RegimeSnapshot.MarketBias.LONG_TAILWIND:
        return 2, -1
    if snapshot.market_bias == RegimeSnapshot.MarketBias.SHORT_TAILWIND:
        return -1, 2
    if snapshot.market_bias == RegimeSnapshot.MarketBias.NOISY:
        return -1, -1
    return 0, 0


def _regime_relation_label(direction: str | None, snapshot: RegimeSnapshot | None) -> str:
    if not snapshot or not direction:
        return "地合い中立"

    if snapshot.market_bias == RegimeSnapshot.MarketBias.NOISY:
        return "地合い荒れ気味"

    if direction == "LONG" and snapshot.market_bias == RegimeSnapshot.MarketBias.LONG_TAILWIND:
        return "地合い追い風"
    if direction == "SHORT" and snapshot.market_bias == RegimeSnapshot.MarketBias.SHORT_TAILWIND:
        return "地合い追い風"
    if direction == "LONG" and snapshot.market_bias == RegimeSnapshot.MarketBias.SHORT_TAILWIND:
        return "地合い逆風"
    if direction == "SHORT" and snapshot.market_bias == RegimeSnapshot.MarketBias.LONG_TAILWIND:
        return "地合い逆風"

    return "地合い中立"


def _short_reason(reason: str) -> str:
    text = (reason or "").strip()

    replacements = [
        ("流れが上向きに変わり始めています", "流れ 上向き転換"),
        ("流れは上向きです", "流れ 上向き"),
        ("流れが下向きに変わり始めています", "流れ 下向き転換"),
        ("流れは下向きです", "流れ 下向き"),
        ("勢いも上向きに変わり始めています", "勢い 上向き転換"),
        ("勢いは上向きです", "勢い 上向き"),
        ("勢いも下向きに変わり始めています", "勢い 下向き転換"),
        ("勢いは下向きです", "勢い 下向き"),
        ("買いがやや優勢です", "買い優勢"),
        ("売りがやや優勢です", "売り優勢"),
        ("売られすぎから持ち直し気味です", "反発気味"),
        ("買われすぎから失速気味です", "失速気味"),
        ("最近の売買コスト帯を上抜けています", "コスト帯を上抜け"),
        ("価格は最近の売買コスト帯より上です", "コスト帯より上"),
        ("最近の売買コスト帯を下抜けています", "コスト帯を下抜け"),
        ("価格は最近の売買コスト帯より下です", "コスト帯より下"),
        ("直近の高値をぬけています", "高値ぬけ"),
        ("高値に近く、もう一段上を試しやすい位置です", "高値に近い"),
        ("直近の安値を割っています", "安値わり"),
        ("安値に近く、もう一段下を試しやすい位置です", "安値に近い"),
        ("出来高も急増していて注目が集まっています", "出来高急増"),
        ("出来高もやや増えています", "出来高やや増"),
        ("大きな流れも上向きです", "大きな流れ 上向き"),
        ("大きな流れは上向きです", "大きな流れ 上向き"),
        ("大きな流れも下向きです", "大きな流れ 下向き"),
        ("大きな流れは下向きです", "大きな流れ 下向き"),
        ("少し上がりすぎで飛びつき注意です", "上がりすぎ注意"),
        ("少し下がりすぎで戻りに注意です", "下がりすぎ注意"),
        ("下ヒゲが強く、押し返しの形です", "下ヒゲ支え"),
        ("上ヒゲが強く、戻り売りに注意です", "上ヒゲ重い"),
        ("上ヒゲが強く、押し戻されやすい形です", "上ヒゲ重い"),
        ("下ヒゲが強く、反発に注意です", "下ヒゲ支え"),
        ("寄り付きは強めでした", "寄り付き強め"),
        ("寄り付きは弱めでした", "寄り付き弱め"),
        ("高値ぬけに出来高が伴っています", "高値ぬけ+出来高"),
        ("安値わりに出来高が伴っています", "安値わり+出来高"),
    ]

    for before, after in replacements:
        if text == before:
            return after

    return _trim_text(text, 18)


def _decision_text(direction: str | None, level: str, row: dict[str, Any], snapshot: RegimeSnapshot | None) -> str:
    regime_relation = _regime_relation_label(direction, snapshot)
    breakout_label = row.get("breakout_human_label") or ""
    distance_label = row.get("distance_human_label") or ""
    volatility_label = row.get("volatility_label") or ""

    if direction == "LONG":
        if regime_relation == "地合い追い風":
            if breakout_label in ("高値ぬけ", "高値に近い"):
                text = "地合いも追い風なので、上抜け確認候補として見やすいです。"
            else:
                text = "個別も地合いもロング寄りなので、継続確認向きです。"
        elif regime_relation == "地合い逆風":
            text = "個別は強めですが、地合いは逆風なので飛びつきすぎは注意です。"
        elif regime_relation == "地合い荒れ気味":
            text = "ロング寄りですが、地合いが荒れ気味なので無理は禁物です。"
        else:
            text = "ロング寄りですが、地合いは中立なので個別の強さ確認が大事です。"
    elif direction == "SHORT":
        if regime_relation == "地合い追い風":
            if breakout_label in ("安値わり", "安値に近い"):
                text = "地合いも追い風なので、下抜け確認候補として見やすいです。"
            else:
                text = "個別も地合いもショート寄りなので、継続確認向きです。"
        elif regime_relation == "地合い逆風":
            text = "個別は弱めですが、地合いは逆風なので売り急ぎは注意です。"
        elif regime_relation == "地合い荒れ気味":
            text = "ショート寄りですが、地合いが荒れ気味なので無理は禁物です。"
        else:
            text = "ショート寄りですが、地合いは中立なので個別の弱さ確認が大事です。"
    else:
        text = "まだ強い一方向ではなく、今は様子見寄りです。"

    if distance_label in ("かなり上がりすぎ", "かなり下がりすぎ"):
        text += " 行きすぎ感もあるので、追いかけすぎは注意です。"
    elif volatility_label == "大きい":
        text += " 値動きが大きいので、位置取りは慎重にしたいです。"

    return _trim_text(text, 90)


def build_candidate_rows(user, limit_per_side: int = 8) -> dict[str, Any]:
    last_regime = ensure_recent_regime_snapshot(user=user, max_age_minutes=90)

    universe_items = list(
        UniverseTicker.objects.filter(user=user, is_active=True).order_by("priority", "ticker")
    )

    ohlc_map = _batch_download_ohlc(universe_items, period="6mo")
    long_adj, short_adj = _regime_adjustments(last_regime)

    all_rows: list[dict[str, Any]] = []

    for item in universe_items:
        ohlc = ohlc_map.get(item.ticker)
        tech = _build_technical_snapshot_from_ohlc(ohlc)
        if not tech.get("has_technical"):
            continue

        base_long_score, long_reasons = _score_long(tech)
        base_short_score, short_reasons = _score_short(tech)

        long_score = base_long_score + long_adj
        short_score = base_short_score + short_adj

        chosen_direction = _pick_direction(long_score, short_score, tech)
        score_total = max(long_score, short_score)
        level = _level_from_score(score_total)

        if not chosen_direction or level == "REFERENCE":
            continue

        selected_reasons = long_reasons if chosen_direction == "LONG" else short_reasons
        selected_reasons_short = [_short_reason(reason) for reason in selected_reasons[:3]]

        if chosen_direction == "LONG":
            action_label = "強いロング候補" if level == "STRONG" else "ロング候補"
        else:
            action_label = "強いショート候補" if level == "STRONG" else "ショート候補"

        row = {
            "ticker": item.ticker,
            "name": item.name or "",
            "priority": item.priority,
            "source_labels": _source_labels(item),
            "source_rank": _source_rank(item),
            "chosen_direction": chosen_direction,
            "level": level,
            "action_label": action_label,
            "score_total": score_total,
            "base_long_score": base_long_score,
            "base_short_score": base_short_score,
            "long_score": long_score,
            "short_score": short_score,
            "last_close": tech["last_close"],
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
            "selected_reasons": selected_reasons,
            "selected_reasons_short": selected_reasons_short,
            "regime_relation_label": _regime_relation_label(chosen_direction, last_regime),
        }
        row["decision_text"] = _decision_text(chosen_direction, level, row, last_regime)
        all_rows.append(row)

    all_rows.sort(
        key=lambda row: (
            LEVEL_RANK.get(row["level"], 9),
            -(row["score_total"]),
            row["source_rank"],
            row["priority"],
            row["ticker"],
        )
    )

    long_rows_all = [row for row in all_rows if row["chosen_direction"] == "LONG"]
    short_rows_all = [row for row in all_rows if row["chosen_direction"] == "SHORT"]

    return {
        "last_regime": last_regime,
        "universe_count": len(universe_items),
        "candidate_total": len(all_rows),
        "long_candidate_count": len(long_rows_all),
        "short_candidate_count": len(short_rows_all),
        "strong_candidate_count": sum(1 for row in all_rows if row["level"] == "STRONG"),
        "attention_candidate_count": sum(1 for row in all_rows if row["level"] == "ATTENTION"),
        "long_rows": long_rows_all[:limit_per_side],
        "short_rows": short_rows_all[:limit_per_side],
        "all_rows": all_rows,
    }