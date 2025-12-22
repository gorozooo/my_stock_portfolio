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


def _is_pro_relevant(rec: Dict[str, Any]) -> bool:
    """
    ✅ 完全PRO主義:
    - 「PRO評価パイプラインに乗っている」レコードだけを“存在するもの”として扱う。
    - 楽天/松井/SBI由来のキーしか無い行は、最初から集計対象外（0件扱い）にする。
    """
    # PRO側の評価キー（どれか1つでもあれば「PROの評価対象」とみなす）
    pro_eval_keys = (
        "eval_label_pro",
        "eval_pl_pro",
        "eval_r_pro",
        "eval_close_px_pro",
        "eval_exit_reason_pro",
    )
    for k in pro_eval_keys:
        if k in rec:
            return True

    # 互換として「共通キー(eval_label等)」をPROとして採用している可能性があるならここで拾う
    # ※もし将来的に共通キーを完全撤廃するなら、このブロックは消してOK
    common_keys = ("eval_label", "eval_pl", "eval_r", "eval_close_px", "eval_exit_reason")
    for k in common_keys:
        if k in rec:
            return True

    return False


def _classify_row_pro_only(rec: Dict[str, Any], today: date) -> Optional[str]:
    """
    完全PRO主義の分類:
      - 未来（pending_future）
      - 評価済（evaluated：win/lose/flat）
    それ以外（skip/unknown 相当）は “数えない” = None を返す
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

    # PRO評価ラベル（新: eval_label_pro / 互換: eval_label）
    v = rec.get("eval_label_pro")
    if v is None:
        v = rec.get("eval_label")

    label = ""
    if isinstance(v, str):
        label = v.strip().lower()

    if label in ("win", "lose", "flat"):
        return "evaluated"

    # ここがポイント：
    # - qtyが0だろうが、labelが無い/Noneだろうが
    #   「PROの評価済/未来」になってないものは “存在しない” 扱い（= カウントしない）
    return None


@dataclass
class BehaviorBanner:
    today_str: str
    counts: Dict[str, int]
    total: int


def build_behavior_banner_summary(*, days: int = 30) -> BehaviorBanner:
    """
    latest_behavior.jsonl をざっくり集計して、Picks上部に出すバナー情報を返す。

    ✅ 完全PRO主義（A）:
    - 楽天/松井/SBI 由来の行は “存在しない” 扱い（集計しない）
    - PROについても「評価済 / 未来」だけを数える
    - skip / unknown は常に 0（= 消える）
    """
    today = timezone.localdate()
    today_str = today.strftime("%Y-%m-%d")

    counts = {
        "evaluated": 0,
        "pending_future": 0,
        "skip": 0,      # 表示互換のためキーは残すが、増やさない
        "unknown": 0,   # 表示互換のためキーは残すが、増やさない
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

        # ✅ PROに関係ない行は「存在しない」扱い
        if not _is_pro_relevant(rec):
            continue

        cat = _classify_row_pro_only(rec, today)
        if cat is None:
            continue

        counts[cat] = counts.get(cat, 0) + 1
        total += 1

    return BehaviorBanner(today_str=today_str, counts=counts, total=total)