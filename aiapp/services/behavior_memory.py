# aiapp/services/behavior_memory.py
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.utils import timezone


Number = Optional[float]

# =========================================================
# PRO一択：side rows の broker は "pro" のみ扱う
# =========================================================
BROKER_KEY_ALLOWED = "pro"


@dataclass
class StatBucket:
    trials: int = 0
    wins: int = 0
    pl_sum: float = 0.0
    r_sum: float = 0.0
    r_cnt: int = 0

    def add(self, is_win: bool, pl: float, r: Number) -> None:
        self.trials += 1
        if is_win:
            self.wins += 1
        self.pl_sum += pl
        if r is not None:
            self.r_sum += float(r)
            self.r_cnt += 1

    def to_dict(self) -> Dict[str, Any]:
        win_rate = (self.wins / self.trials * 100.0) if self.trials > 0 else None
        avg_pl = (self.pl_sum / self.trials) if self.trials > 0 else None
        avg_r = (self.r_sum / self.r_cnt) if self.r_cnt > 0 else None
        return {
            "trials": self.trials,
            "wins": self.wins,
            "win_rate": win_rate,
            "avg_pl": avg_pl,
            "avg_r": avg_r,
        }


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _bucket_time_of_day(ts_str: str) -> str:
    if not ts_str:
        return "その他"
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        dt = timezone.localtime(dt)
    except Exception:
        return "その他"

    h = dt.hour * 60 + dt.minute
    if 9 * 60 <= h < 11 * 60 + 30:
        return "前場寄り〜11:30"
    if 11 * 60 + 30 <= h < 13 * 60:
        return "お昼〜後場寄り"
    if 13 * 60 <= h <= 15 * 60:
        return "後場〜大引け"
    return "時間外/その他"


def _bucket_atr_pct(atr: Number) -> str:
    if atr is None:
        return "ATR:不明"
    if atr < 1.0:
        return "ATR:〜1%"
    if atr < 2.0:
        return "ATR:1〜2%"
    if atr < 3.0:
        return "ATR:2〜3%"
    return "ATR:3%以上"


def _bucket_slope(slope: Number) -> str:
    if slope is None:
        return "傾き:不明"
    if slope < 0:
        return "傾き:下向き"
    if slope < 5:
        return "傾き:緩やかな上向き"
    if slope < 10:
        return "傾き:強めの上向き"
    return "傾き:急騰寄り"


def _load_latest_side_rows(user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    latest_behavior_side.jsonl を読み込んで、
    （必要なら）user_id でフィルタして返す。
    """
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    latest_side = behavior_dir / "latest_behavior_side.jsonl"

    if not latest_side.exists():
        return []

    rows: List[Dict[str, Any]] = []
    try:
        text = latest_side.read_text(encoding="utf-8")
    except Exception:
        return []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if user_id is not None and rec.get("user_id") != user_id:
            continue
        rows.append(rec)

    return rows


def build_behavior_memory(user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    latest_behavior_side.jsonl から「クセの地図」を構築して dict で返す。

    ✅ PRO一択：
    - broker == "pro" の行だけ採用（旧データ混入対策）
    """
    rows = _load_latest_side_rows(user_id=user_id)
    if not rows:
        return {
            "user_id": user_id,
            "total_trades": 0,
            "updated_at": timezone.now().isoformat(),
            "broker": {},
            "sector": {},
            "time_of_day": {},
            "atr_bucket": {},
            "slope_bucket": {},
            "trend_daily": {},
        }

    broker_stats: Dict[str, StatBucket] = defaultdict(StatBucket)
    sector_stats: Dict[str, StatBucket] = defaultdict(StatBucket)
    time_stats: Dict[str, StatBucket] = defaultdict(StatBucket)
    atr_stats: Dict[str, StatBucket] = defaultdict(StatBucket)
    slope_stats: Dict[str, StatBucket] = defaultdict(StatBucket)
    trend_stats: Dict[str, StatBucket] = defaultdict(StatBucket)

    total = 0

    for r in rows:
        broker_key = str(r.get("broker") or "unknown").lower()
        if broker_key != BROKER_KEY_ALLOWED:
            # PRO以外は一切学習対象にしない
            continue

        label = (r.get("eval_label") or "").lower()
        if label not in ("win", "lose", "flat"):
            continue

        qty = _safe_float(r.get("qty")) or 0.0
        if qty <= 0:
            continue

        total += 1
        is_win = (label == "win")
        pl = _safe_float(r.get("eval_pl")) or 0.0
        r_val = _safe_float(r.get("eval_r"))

        sector_key = str(r.get("sector") or "(未分類)")
        time_key = _bucket_time_of_day(str(r.get("ts") or ""))
        atr_key = _bucket_atr_pct(_safe_float(r.get("atr_14")))
        slope_key = _bucket_slope(_safe_float(r.get("slope_20")))
        trend_key = str(r.get("trend_daily") or "不明")

        broker_stats[BROKER_KEY_ALLOWED].add(is_win, pl, r_val)
        sector_stats[sector_key].add(is_win, pl, r_val)
        time_stats[time_key].add(is_win, pl, r_val)
        atr_stats[atr_key].add(is_win, pl, r_val)
        slope_stats[slope_key].add(is_win, pl, r_val)
        trend_stats[trend_key].add(is_win, pl, r_val)

    def _dump(stats: Dict[str, StatBucket]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, sb in stats.items():
            out[key] = sb.to_dict()
        return out

    return {
        "user_id": user_id,
        "total_trades": total,
        "updated_at": timezone.now().isoformat(),
        "broker": _dump(broker_stats),
        "sector": _dump(sector_stats),
        "time_of_day": _dump(time_stats),
        "atr_bucket": _dump(atr_stats),
        "slope_bucket": _dump(slope_stats),
        "trend_daily": _dump(trend_stats),
    }


def save_behavior_memory(user_id: Optional[int] = None) -> Path:
    """
    build_behavior_memory の結果を JSON に保存して Path を返す。
    """
    memory = build_behavior_memory(user_id=user_id)

    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    memory_dir = behavior_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    today = timezone.localdate().strftime("%Y%m%d")
    uid = memory["user_id"] or "all"

    out_path = memory_dir / f"{today}_behavior_memory_u{uid}.json"
    latest_path = memory_dir / f"latest_behavior_memory_u{uid}.json"

    text = json.dumps(memory, ensure_ascii=False, indent=2)
    out_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")

    return latest_path