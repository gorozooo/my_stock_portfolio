# aiapp/management/commands/rebuild_behavior_stats.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
rebuild_behavior_stats

/media/aiapp/behavior/latest_behavior.jsonl を読み込み、
紙シミュ（DEMO）の行動結果を「証券会社もモードも関係なし」に合算集計して
aiapp.models.behavior_stats.BehaviorStats を upsert する。

仕様（あなたの要件に合わせて固定）:
- 学習対象: DEMO（紙シミュ）だけ（--include-live で混ぜられる）
- broker: rakuten/matsui を合算して学習（分けない）
- mode_period/mode_aggr: JSONLに無いので「all/all」に固定（＝モード関係なしを厳密に担保）
- stars: 本番品質（B方針）: 有効試行 n < 10 は ⭐️1 固定

使い方:
  python manage.py rebuild_behavior_stats
  python manage.py rebuild_behavior_stats --days 90
  python manage.py rebuild_behavior_stats --dry-run
  python manage.py rebuild_behavior_stats --include-live
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


def _norm_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()


# -------------------------
# record
# -------------------------
@dataclass
class Rec:
    user_id: Optional[int]
    sim_mode: str          # "demo" / "live" / other
    code: str
    price_date: Optional[date]
    entry: Optional[float]
    qty_r: float
    qty_m: float
    label_r: Optional[str]
    pl_r: Optional[float]
    label_m: Optional[str]
    pl_m: Optional[float]


def _stars_by_rule(win_rate: float, n: int) -> int:
    """
    B方針（本番品質）:
    - 有効試行 n < 10 は ⭐️1 固定
    - それ以上で勝率ルール
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


def _load_jsonl(
    *,
    days: int,
    user_filter: Optional[int],
    include_live: bool,
) -> List[Rec]:
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

        sim_mode = _norm_str(d.get("mode")).lower()  # demo/live
        if not include_live:
            if sim_mode != "demo":
                continue

        code = _norm_str(d.get("code"))
        if not code:
            continue

        pd = _parse_ymd(_norm_str(d.get("price_date"))) or _parse_ymd(_norm_str(d.get("run_date")))
        if pd is not None and pd < cutoff:
            continue

        entry = _safe_float(d.get("entry"))
        qty_r = _safe_float(d.get("qty_rakuten")) or 0.0
        qty_m = _safe_float(d.get("qty_matsui")) or 0.0

        # 重複キー（あなたの思想：同条件は1件扱い）
        key = (
            sim_mode,
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
                sim_mode=sim_mode or "",
                code=code,
                price_date=pd,
                entry=entry,
                qty_r=qty_r,
                qty_m=qty_m,
                label_r=(_norm_str(d.get("eval_label_rakuten")).lower() if d.get("eval_label_rakuten") is not None else None),
                pl_r=_safe_float(d.get("eval_pl_rakuten")),
                label_m=(_norm_str(d.get("eval_label_matsui")).lower() if d.get("eval_label_matsui") is not None else None),
                pl_m=_safe_float(d.get("eval_pl_matsui")),
            )
        )

    return recs


def _collect_combined_stats(recs: List[Rec]) -> Dict[str, Dict[str, Any]]:
    """
    code -> stats

    brokerは合算:
      - 楽天が有効なら楽天を1試行
      - 松井が有効なら松井を1試行
    ※両方有効なら2試行として数える（別ブローカーの結果を「全部育てる」ため）
    """
    out: Dict[str, Dict[str, Any]] = {}

    def add_one(code: str, label: Optional[str], qty: float, pl: Optional[float]) -> None:
        if qty == 0 or qty is None:
            return
        if label not in ("win", "lose", "flat"):
            return

        st = out.setdefault(
            code,
            {"n": 0, "win": 0, "lose": 0, "flat": 0, "sum_pl": 0.0, "cnt_pl": 0},
        )
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

    for r in recs:
        add_one(r.code, r.label_r, r.qty_r, r.pl_r)
        add_one(r.code, r.label_m, r.qty_m, r.pl_m)

    for code, st in out.items():
        n = st["n"]
        win = st["win"]
        st["win_rate"] = round(100.0 * win / n, 1) if n > 0 else 0.0
        st["avg_pl"] = round(st["sum_pl"] / st["cnt_pl"], 1) if st["cnt_pl"] > 0 else None

    return out


class Command(BaseCommand):
    help = "紙シミュ（DEMO）の学習結果を合算して BehaviorStats を更新する（証券会社もモードも関係なし）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=90, help="何日分を見るか（default 90）")
        parser.add_argument("--user", type=int, default=None, help="user_idで絞り込み（任意）")
        parser.add_argument("--include-live", action="store_true", help="LIVEも混ぜる（デフォルトはDEMOのみ）")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せず表示のみ")

    def handle(self, *args, **options) -> None:
        days: int = options["days"]
        user_filter: Optional[int] = options["user"]
        include_live: bool = bool(options["include_live"])
        dry_run: bool = bool(options["dry_run"])

        recs = _load_jsonl(days=days, user_filter=user_filter, include_live=include_live)
        if not recs:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] 対象レコードがありません。"))
            return

        stats_by_code = _collect_combined_stats(recs)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== rebuild_behavior_stats preview (ALL combined) ====="))
        self.stdout.write(f"  days={days}  include_live={include_live}  dry_run={dry_run}")
        if user_filter is not None:
            self.stdout.write(f"  user_id={user_filter}")
        self.stdout.write(f"  unique_codes={len(stats_by_code)}")

        preview = sorted(stats_by_code.items(), key=lambda kv: kv[1]["n"], reverse=True)[:30]
        for code, st in preview:
            stars = _stars_by_rule(float(st["win_rate"]), int(st["n"]))
            self.stdout.write(
                f"  {code} [all/all]: n={st['n']:3d} win_rate={st['win_rate']:5.1f}% avg_pl={st['avg_pl']} -> stars={stars}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] dry-run のためDB更新は行いません。"))
            return

        # DBへ upsert（mode_period/mode_aggr は all/all 固定）
        mode_period = "all"
        mode_aggr = "all"

        with transaction.atomic():
            upserted = 0
            for code, st in stats_by_code.items():
                win_rate = float(st["win_rate"])
                n = int(st["n"])
                stars = int(_stars_by_rule(win_rate, n))

                lookup_kwargs = {"code": str(code), "mode_period": mode_period, "mode_aggr": mode_aggr}
                if _has_field(BehaviorStats, "broker"):
                    lookup_kwargs["broker"] = "all"

                obj, _created = BehaviorStats.objects.get_or_create(**lookup_kwargs)

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