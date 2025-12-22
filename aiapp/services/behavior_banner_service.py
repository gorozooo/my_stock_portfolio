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


def _safe_float(v: Any) -> float:
    if v in (None, "", "null"):
        return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


def _classify_row(rec: Dict[str, Any], today: date) -> str:
    """
    1レコードをカテゴリに分類する（PRO専用）。
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

    # --- PROの評価が「存在する」か？（どれか1つでも入っていれば評価パイプライン上は認識する） ---
    has_eval = (
        ("eval_label_pro" in rec)
        or ("eval_pl_pro" in rec)
        or ("eval_r_pro" in rec)
        or ("eval_close_px" in rec)
        or ("eval_exit_reason" in rec)
    )
    if not has_eval:
        return "unknown"

    # --- PRO 数量0は skip（=評価対象外） ---
    qty_pro = _safe_float(rec.get("qty_pro"))
    # 念のため、古い "qty" だけ来るデータが混ざった場合は PRO扱いに寄せる
    if qty_pro == 0.0:
        qty_pro = _safe_float(rec.get("qty"))

    if qty_pro == 0.0:
        return "skip"

    # --- PRO 勝敗ラベル ---
    v = rec.get("eval_label_pro")
    if v is None:
        v = rec.get("eval_label")  # 予備（もし生成側が共通キーを使う場合）

    label = ""
    if isinstance(v, str):
        label = v.lower().strip()

    if label in ("win", "lose", "flat"):
        return "evaluated"

    return "unknown"


@dataclass
class BehaviorBanner:
    today_str: str
    counts: Dict[str, int]
    total: int


def build_behavior_banner_summary(*, days: int = 30) -> BehaviorBanner:
    """
    latest_behavior.jsonl をざっくり集計して、Picks上部に出すバナー情報を返す（PRO専用）。
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