# aiapp/services/behavior_banner_service.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, Optional

from django.conf import settings
from django.utils import timezone

JST = dt_timezone(timedelta(hours=9))


def _to_date_any(v: Any) -> Optional[date]:
    """
    JSONL内の trade_date / run_date / price_date / ts などを date に寄せる。
    """
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.date()
        return v.astimezone(JST).date()

    if not isinstance(v, str) or not v:
        return None

    s = v.strip()

    # "YYYY-MM-DD"
    try:
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return date.fromisoformat(s[:10])
    except Exception:
        pass

    # "YYYY-MM-DDTHH:MM:SS+09:00" / "Z"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.date()
        return dt.astimezone(JST).date()
    except Exception:
        return None


def _norm_code(code: Any) -> str:
    s = str(code or "").strip()
    if s.endswith(".T"):
        s = s[:-2]
    return s


def _read_latest_behavior_path() -> Path:
    return Path(settings.MEDIA_ROOT) / "aiapp" / "behavior" / "latest_behavior.jsonl"


def _classify_row(rec: Dict[str, Any], today: date) -> str:
    """
    1レコードをカテゴリに分類する。
    """
    # 未来評価（例：trade_date/run_date/price_date/ts が未来）
    td = (
        _to_date_any(rec.get("trade_date"))
        or _to_date_any(rec.get("run_date"))
        or _to_date_any(rec.get("price_date"))
        or _to_date_any(rec.get("ts"))
    )
    if td is not None and td > today:
        return "pending_future"

    has_eval = (
        ("eval_label_rakuten" in rec)
        or ("eval_label_matsui" in rec)
        or ("eval_close_px" in rec)
        or ("eval_exit_reason" in rec)
    )
    if not has_eval:
        return "unknown"

    # 数量ゼロ系は skip
    qty_r = rec.get("qty_rakuten") or 0
    qty_m = rec.get("qty_matsui") or 0
    try:
        qty_r = float(qty_r)
    except Exception:
        qty_r = 0.0
    try:
        qty_m = float(qty_m)
    except Exception:
        qty_m = 0.0

    if qty_r == 0.0 and qty_m == 0.0:
        return "skip"

    labels = set()
    for k in ("eval_label_rakuten", "eval_label_matsui"):
        v = rec.get(k)
        if isinstance(v, str) and v:
            labels.add(v.lower().strip())

    if labels & {"win", "lose", "flat"}:
        return "evaluated"

    return "unknown"


@dataclass
class BehaviorBanner:
    today_str: str
    counts: Dict[str, int]
    total: int


def build_behavior_banner_summary(*, days: int = 30) -> BehaviorBanner:
    """
    latest_behavior.jsonl をざっくり集計して、Picks上部に出すバナー情報を返す。
    """
    today = timezone.localdate()
    today_str = today.strftime("%Y-%m-%d")

    counts = {
        "evaluated": 0,
        "pending_future": 0,
        "skip": 0,
        "unknown": 0,
    }
    total = 0

    path = _read_latest_behavior_path()
    if not path.exists():
        return BehaviorBanner(today_str=today_str, counts=counts, total=0)

    cutoff = today - timedelta(days=int(days))

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return BehaviorBanner(today_str=today_str, counts=counts, total=0)

    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            continue

        code = _norm_code(rec.get("code"))
        if not code:
            continue

        d = (
            _to_date_any(rec.get("trade_date"))
            or _to_date_any(rec.get("run_date"))
            or _to_date_any(rec.get("price_date"))
            or _to_date_any(rec.get("ts"))
        )
        if d is not None and d < cutoff:
            continue

        cat = _classify_row(rec, today)
        counts[cat] = counts.get(cat, 0) + 1
        total += 1

    return BehaviorBanner(today_str=today_str, counts=counts, total=total)