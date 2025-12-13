# aiapp/management/commands/rebuild_behavior_stats.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
rebuild_behavior_stats

/media/aiapp/behavior/latest_behavior.jsonl を読み込み、
銘柄ごとの行動結果を集計して aiapp.models.behavior_stats.BehaviorStats を upsert する。

ポイント:
- JSONLに result_r が無い場合が多いので、まずは label(win/lose/flat) と eval_pl_* を使う
- stars は「データ不足は 1」に倒す安全設計（本番向き）
- BehaviorStats のモデルフィールドは環境差があり得るので、
  存在するフィールドだけ安全に更新する（無ければ無視）

使い方:
  python manage.py rebuild_behavior_stats --days 90 --broker rakuten --mode_period short --mode_aggr aggr
  python manage.py rebuild_behavior_stats --dry-run
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from aiapp.models.behavior_stats import BehaviorStats


# -------------------------
# utils
# -------------------------
def _safe_float(x: Any) -> Optional[float]:
    if x in (None, "", "null"):
        return None
    try:
        v = float(x)
        if not np.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _safe_int(x: Any) -> Optional[int]:
    if x in (None, "", "null"):
        return None
    try:
        return int(x)
    except Exception:
        return None


def _has_field(model, field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _set_if_exists(obj, field: str, value: Any) -> None:
    if _has_field(obj.__class__, field):
        setattr(obj, field, value)


def _parse_ymd(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


# -------------------------
# record
# -------------------------
@dataclass
class Rec:
    user_id: Optional[int]
    mode: Optional[str]
    code: str
    price_date: Optional[date]
    qty_r: float
    qty_m: float
    label_r: Optional[str]
    pl_r: Optional[float]
    label_m: Optional[str]
    pl_m: Optional[float]


def _load_jsonl(days: int, user_filter: Optional[int] = None) -> List[Rec]:
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    path = behavior_dir / "latest_behavior.jsonl"
    if not path.exists():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []

    cutoff = date.today() - timedelta(days=days)

    recs: List[Rec] = []
    # 重複除外（あなたの behavior_stats.py と同じ思想）
    seen: set[Tuple] = set()

    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue

        uid = _safe_int(d.get("user_id"))
        if user_filter is not None and uid != user_filter:
            continue

        mode = (d.get("mode") or "").lower() if isinstance(d.get("mode"), str) else None
        code = str(d.get("code") or "").strip()
        if not code:
            continue

        pd = _parse_ymd(str(d.get("price_date") or "").strip())
        if pd is not None and pd < cutoff:
            continue

        entry = _safe_float(d.get("entry"))
        qty_r = _safe_float(d.get("qty_rakuten")) or 0.0
        qty_m = _safe_float(d.get("qty_matsui")) or 0.0

        key = (
            mode or "",
            code,
            pd.isoformat() if pd else "",
            round(entry if entry is not None else 0.0, 2),
            round(qty_r, 0),
            round(qty_m, 0),
        )
        if key in seen:
            continue
        seen.add(key)

        recs.append(
            Rec(
                user_id=uid,
                mode=mode,
                code=code,
                price_date=pd,
                qty_r=qty_r,
                qty_m=qty_m,
                label_r=(str(d.get("eval_label_rakuten")) if d.get("eval_label_rakuten") is not None else None),
                pl_r=_safe_float(d.get("eval_pl_rakuten")),
                label_m=(str(d.get("eval_label_matsui")) if d.get("eval_label_matsui") is not None else None),
                pl_m=_safe_float(d.get("eval_pl_matsui")),
            )
        )
    return recs


def _stars_by_rule(win_rate: float, n: int) -> int:
    """
    “データ不足は1” を徹底する安全ルール。
    必要ならここを本番仕様（平均Rなど）に置き換える。
    """
    if n < 10:
        return 1
    if win_rate >= 60.0:
        return 5
    if win_rate >= 55.0:
        return 4
    if win_rate >= 50.0:
        return 3
    if win_rate >= 45.0:
        return 2
    return 1


def _collect_for_broker(recs: List[Rec], broker: str) -> Dict[str, Dict[str, Any]]:
    """
    code -> stats
    stats: n_effective, win, lose, flat, win_rate, avg_pl, sum_pl
    """
    out: Dict[str, Dict[str, Any]] = {}
    for r in recs:
        if broker == "rakuten":
            qty = r.qty_r
            label = (r.label_r or "").lower() if r.label_r else None
            pl = r.pl_r
        else:
            qty = r.qty_m
            label = (r.label_m or "").lower() if r.label_m else None
            pl = r.pl_m

        # ポジ無しは統計から除外（stars判定にも入れない）
        if qty == 0 or qty is None:
            continue
        if label not in ("win", "lose", "flat"):
            continue

        st = out.setdefault(r.code, {"n": 0, "win": 0, "lose": 0, "flat": 0, "sum_pl": 0.0, "cnt_pl": 0})
        st["n"] += 1
        if label == "win":
            st["win"] += 1
        elif label == "lose":
            st["lose"] += 1
        else:
            st["flat"] += 1

        if pl is not None:
            st["sum_pl"] += float(pl)
            st["cnt_pl"] += 1

    for code, st in out.items():
        n = st["n"]
        win = st["win"]
        st["win_rate"] = round(100.0 * win / n, 1) if n > 0 else 0.0
        st["avg_pl"] = round(st["sum_pl"] / st["cnt_pl"], 1) if st["cnt_pl"] > 0 else None
    return out


class Command(BaseCommand):
    help = "latest_behavior.jsonl から BehaviorStats を再集計してDBへ反映する（90日など指定可）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=90, help="何日分を見るか（default 90）")
        parser.add_argument("--user", type=int, default=None, help="user_idで絞り込み（任意）")
        parser.add_argument("--broker", type=str, default="rakuten", choices=["rakuten", "matsui"], help="どちらの評価で集計するか")
        parser.add_argument("--mode_period", type=str, default="short", help="DB側に保存する mode_period（例 short/long）")
        parser.add_argument("--mode_aggr", type=str, default="aggr", help="DB側に保存する mode_aggr（例 aggr/normal/def）")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せず表示のみ")

    def handle(self, *args, **options) -> None:
        days: int = options["days"]
        user_filter: Optional[int] = options["user"]
        broker: str = options["broker"]
        mode_period: str = (options["mode_period"] or "short").strip().lower()
        mode_aggr: str = (options["mode_aggr"] or "aggr").strip().lower()
        dry_run: bool = bool(options["dry_run"])

        recs = _load_jsonl(days=days, user_filter=user_filter)
        if not recs:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] 対象レコードがありません。"))
            return

        stats_by_code = _collect_for_broker(recs, broker=broker)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== rebuild_behavior_stats preview ====="))
        self.stdout.write(f"  days={days}  broker={broker}  mode_period={mode_period}  mode_aggr={mode_aggr}  dry_run={dry_run}")
        if user_filter is not None:
            self.stdout.write(f"  user_id={user_filter}")
        self.stdout.write(f"  unique_codes={len(stats_by_code)}")

        # 上位プレビュー（試行回数多い順）
        preview = sorted(stats_by_code.items(), key=lambda kv: kv[1]["n"], reverse=True)[:20]
        for code, st in preview:
            stars = _stars_by_rule(st["win_rate"], st["n"])
            self.stdout.write(f"  {code}: n={st['n']:3d} win_rate={st['win_rate']:5.1f}% avg_pl={st['avg_pl']} -> stars={stars}")

        if dry_run:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] dry-run のためDB更新は行いません。"))
            return

        # DBへ upsert
        with transaction.atomic():
            upserted = 0
            for code, st in stats_by_code.items():
                win_rate = float(st["win_rate"])
                n = int(st["n"])
                stars = int(_stars_by_rule(win_rate, n))

                obj, _created = BehaviorStats.objects.get_or_create(
                    code=str(code),
                    mode_period=mode_period,
                    mode_aggr=mode_aggr,
                )

                # 存在するフィールドだけ安全に入れる
                _set_if_exists(obj, "stars", stars)
                _set_if_exists(obj, "win_rate", win_rate)
                _set_if_exists(obj, "n", n)
                _set_if_exists(obj, "trials", n)
                _set_if_exists(obj, "win", int(st["win"]))
                _set_if_exists(obj, "lose", int(st["lose"]))
                _set_if_exists(obj, "flat", int(st["flat"]))
                _set_if_exists(obj, "avg_pl", st["avg_pl"])
                _set_if_exists(obj, "sum_pl", float(st["sum_pl"]))

                obj.save()
                upserted += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[rebuild_behavior_stats] DB更新完了: {upserted} 件 upsert"))