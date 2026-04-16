# =========================================================
# [FILE] snapshot_writer.py
# [PATH] <project_root>/tradeai/services/learning/snapshot_writer.py
#
# このファイルは何？
# - tradeai の監視結果を、学習用スナップショットとして JSONL 保存するサービスです。
# - 通知した行だけでなく、通知しなかった行も残します。
# - 後から「何が効いたか」「見送りが正しかったか」を評価する土台になります。
# =========================================================

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone


def _safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _local_now():
    return timezone.localtime()


def _ymd_parts():
    now = _local_now()
    return now.strftime("%Y-%m"), now.strftime("%Y-%m-%d"), now.isoformat()


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _ensure_dir(path)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _watch_path() -> Path:
    ym, ymd, _iso = _ymd_parts()
    return Path(settings.MEDIA_ROOT) / "tradeai" / "learning" / "watch_signals" / ym / f"{ymd}.jsonl"


def _holding_path() -> Path:
    ym, ymd, _iso = _ymd_parts()
    return Path(settings.MEDIA_ROOT) / "tradeai" / "learning" / "holding_signals" / ym / f"{ymd}.jsonl"


def build_watch_snapshot_record(user, row: dict[str, Any]) -> dict[str, Any]:
    _ym, _ymd, captured_at = _ymd_parts()

    return {
        "captured_at": captured_at,
        "source": "watchlist_monitor",
        "user_id": getattr(user, "id", None),
        "username": getattr(user, "username", ""),
        "ticker": row.get("ticker") or "",
        "name": row.get("name") or "",
        "chosen_direction": row.get("chosen_direction"),
        "level": row.get("level"),
        "action_label": row.get("action_label"),
        "emit_event": bool(row.get("emit_event")),
        "is_candidate": bool(row.get("emit_event")),
        "priority": _safe_int(row.get("priority")),
        "long_enabled": bool(row.get("long_enabled")),
        "short_enabled": bool(row.get("short_enabled")),
        "notify_enabled": bool(row.get("notify_enabled")),
        "long_score": _safe_int(row.get("long_score")),
        "short_score": _safe_int(row.get("short_score")),
        "summary_text": row.get("summary_text") or "",
        "selected_reasons": list(row.get("selected_reasons") or []),

        # 初心者向け翻訳ラベル
        "flow_label": row.get("flow_label"),
        "momentum_label": row.get("momentum_label"),
        "timing_label": row.get("timing_label"),
        "volatility_label": row.get("volatility_label"),
        "cost_position_label": row.get("cost_position_label"),
        "volume_human_label": row.get("volume_human_label"),
        "breakout_human_label": row.get("breakout_human_label"),
        "big_flow_label": row.get("big_flow_label"),
        "distance_human_label": row.get("distance_human_label"),
        "wick_human_label": row.get("wick_human_label"),
        "gap_human_label": row.get("gap_human_label"),

        # 内部判定
        "ma_state": row.get("ma_state"),
        "ma_label": row.get("ma_label"),
        "macd_state": row.get("macd_state"),
        "macd_label": row.get("macd_label"),
        "rsi_state": row.get("rsi_state"),
        "rsi_label": row.get("rsi_label"),
        "vwap_state": row.get("vwap_state"),
        "vwap_label": row.get("vwap_label"),
        "volume_state": row.get("volume_state"),
        "volume_label": row.get("volume_label"),
        "breakout_state": row.get("breakout_state"),
        "breakout_label": row.get("breakout_label"),
        "ichimoku_state": row.get("ichimoku_state"),
        "ichimoku_label": row.get("ichimoku_label"),
        "ma_distance_state": row.get("ma_distance_state"),
        "ma_distance_label": row.get("ma_distance_label"),
        "wick_state": row.get("wick_state"),
        "wick_label": row.get("wick_label"),
        "gap_state": row.get("gap_state"),
        "gap_label": row.get("gap_label"),

        # 数値
        "last_close": _safe_float(row.get("last_close")),
        "rsi": _safe_float(row.get("rsi")),
        "atr": _safe_float(row.get("atr")),
        "atr_pct": _safe_float(row.get("atr_pct")),
        "vwap": _safe_float(row.get("vwap")),
        "volume_ratio": _safe_float(row.get("volume_ratio")),
        "range_high": _safe_float(row.get("range_high")),
        "range_low": _safe_float(row.get("range_low")),
        "tenkan": _safe_float(row.get("tenkan")),
        "kijun": _safe_float(row.get("kijun")),
        "span_a": _safe_float(row.get("span_a")),
        "span_b": _safe_float(row.get("span_b")),
        "ma_value": _safe_float(row.get("ma_value")),
        "distance_pct": _safe_float(row.get("distance_pct")),
        "upper_wick_pct": _safe_float(row.get("upper_wick_pct")),
        "lower_wick_pct": _safe_float(row.get("lower_wick_pct")),
        "gap_pct": _safe_float(row.get("gap_pct")),
    }


def build_holding_snapshot_record(user, row: dict[str, Any]) -> dict[str, Any]:
    _ym, _ymd, captured_at = _ymd_parts()

    return {
        "captured_at": captured_at,
        "source": "holding_monitor",
        "user_id": getattr(user, "id", None),
        "username": getattr(user, "username", ""),
        "ticker": row.get("ticker") or "",
        "name": row.get("name") or "",
        "direction": row.get("direction"),
        "direction_label": row.get("direction_label"),
        "level": row.get("level"),
        "action_key": row.get("action_key"),
        "action_label": row.get("action_label"),
        "emit_event": bool(row.get("emit_event")),
        "is_candidate": bool(row.get("emit_event")),
        "broker": row.get("broker"),
        "broker_label": row.get("broker_label"),
        "account": row.get("account"),
        "account_label": row.get("account_label"),
        "quantity": _safe_int(row.get("quantity")),
        "hold_days": _safe_int(row.get("hold_days")),
        "summary_text": row.get("reason_text") or "",

        # 数値
        "avg_cost": _safe_float(row.get("avg_cost")),
        "last_price": _safe_float(row.get("last_price")),
        "pnl_pct": _safe_float(row.get("pnl_pct")),
        "pnl_yen": _safe_float(row.get("pnl_yen")),
        "short_ma": _safe_float(row.get("short_ma")),
        "long_ma": _safe_float(row.get("long_ma")),
        "atr": _safe_float(row.get("atr")),
        "atr_pct": _safe_float(row.get("atr_pct")),

        # 内部判定
        "ma_state": row.get("ma_state"),
        "ma_label": row.get("ma_label"),
        "technical_summary": row.get("technical_summary"),
    }


def append_watch_snapshots(user, rows: list[dict[str, Any]]) -> tuple[str, int]:
    records = [build_watch_snapshot_record(user, row) for row in rows]
    path = _watch_path()
    _append_jsonl(path, records)
    return str(path), len(records)


def append_holding_snapshots(user, rows: list[dict[str, Any]]) -> tuple[str, int]:
    records = [build_holding_snapshot_record(user, row) for row in rows]
    path = _holding_path()
    _append_jsonl(path, records)
    return str(path), len(records)