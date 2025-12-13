# aiapp/management/commands/rebuild_behavior_stats.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
rebuild_behavior_stats

/media/aiapp/behavior/latest_behavior.jsonl を読み込み、
紙シミュ（DEMO）の行動結果を「証券会社もモードも関係なし」に集計して
aiapp.models.behavior_stats.BehaviorStats を upsert する。

重要:
- broker は分けずに合算（rakuten/matsui の結果を全部使う）
- mode_period/mode_aggr は JSONL に入っていれば自動で拾って group 化する
  入っていなければ、--default-mode-period / --default-mode-aggr の値で保存する
- stars は B方針（本番品質）: 有効試行 n < 10 は ⭐️1 固定

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
    mode_period: str       # "short"/"long"/...
    mode_aggr: str         # "aggr"/"normal"/"def"/...
    ts: Optional[str]


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
    default_mode_period: str,
    default_mode_aggr: str,
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
            # 紙シミュのみ
            if sim_mode != "demo":
                continue

        code = _norm_str(d.get("code"))
        if not code:
            continue

        pd = _parse_ymd(_norm_str(d.get("price_date")))
        if pd is not None and pd < cutoff:
            continue

        entry = _safe_float(d.get("entry"))
        qty_r = _safe_float(d.get("qty_rakuten")) or 0.0
        qty_m = _safe_float(d.get("qty_matsui")) or 0.0

        # mode_period/mode_aggr がJSONLにあれば採用。無ければデフォルト。
        mode_period = _norm_str(d.get("mode_period")).lower() or default_mode_period
        mode_aggr = _norm_str(d.get("mode_aggr")).lower() or default_mode_aggr

        # 重複キー（あなたの設計思想を踏襲）
        key = (
            sim_mode,
            code,
            pd.isoformat() if pd else "",
            round(entry if entry is not None else 0.0, 2),
            round(qty_r, 0),
            round(qty_m, 0),
            mode_period,
            mode_aggr,
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
                mode_period=mode_period,
                mode_aggr=mode_aggr,
                ts=d.get("ts"),
            )
        )

    return recs


def _collect_combined_stats(recs: List[Rec]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """
    (code, mode_period, mode_aggr) -> stats

    brokerは合算:
      - 楽天側が有効なら楽天の結果を1試行として採用
      - 松井側が有効なら松井の結果を1試行として採用
    ※両方有効なら2試行として数える（別条件としてログが分かれている設計なので自然）
    """
    out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def add_one(key: Tuple[str, str, str], label: Optional[str], qty: float, pl: Optional[float]) -> None:
        if qty == 0 or qty is None:
            return
        if label not in ("win", "lose", "flat"):
            return

        st = out.setdefault(
            key,
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
        key = (r.code, r.mode_period, r.mode_aggr)

        # 楽天の試行
        add_one(key, r.label_r, r.qty_r, r.pl_r)
        # 松井の試行
        add_one(key, r.label_m, r.qty_m, r.pl_m)

    # 率など派生を付与
    for key, st in out.items():
        n = st["n"]
        win = st["win"]
        st["win_rate"] = round(100.0 * win / n, 1) if n > 0 else 0.0
        st["avg_pl"] = round(st["sum_pl"] / st["cnt_pl"], 1) if st["cnt_pl"] > 0 else None

    return out


class Command(BaseCommand):
    help = "紙シミュ（DEMO）の学習結果を全て集計して BehaviorStats を更新する（broker/モードに依存しない）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=90, help="何日分を見るか（default 90）")
        parser.add_argument("--user", type=int, default=None, help="user_idで絞り込み（任意）")
        parser.add_argument("--include-live", action="store_true", help="LIVEも混ぜる（デフォルトはDEMOのみ）")
        parser.add_argument("--default-mode-period", type=str, default="short", help="JSONLにmode_periodが無い場合の保存値")
        parser.add_argument("--default-mode-aggr", type=str, default="aggr", help="JSONLにmode_aggrが無い場合の保存値")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せず表示のみ")

    def handle(self, *args, **options) -> None:
        days: int = options["days"]
        user_filter: Optional[int] = options["user"]
        include_live: bool = bool(options["include_live"])
        default_mode_period: str = (_norm_str(options["default_mode_period"]).lower() or "short")
        default_mode_aggr: str = (_norm_str(options["default_mode_aggr"]).lower() or "aggr")
        dry_run: bool = bool(options["dry_run"])

        recs = _load_jsonl(
            days=days,
            user_filter=user_filter,
            include_live=include_live,
            default_mode_period=default_mode_period,
            default_mode_aggr=default_mode_aggr,
        )

        if not recs:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] 対象レコードがありません。"))
            return

        stats = _collect_combined_stats(recs)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== rebuild_behavior_stats preview (combined) ====="))
        self.stdout.write(f"  days={days}  include_live={include_live}  dry_run={dry_run}")
        if user_filter is not None:
            self.stdout.write(f"  user_id={user_filter}")
        self.stdout.write(f"  groups(code×mode_period×mode_aggr)={len(stats)}")

        # プレビュー（試行回数多い順）
        preview = sorted(stats.items(), key=lambda kv: kv[1]["n"], reverse=True)[:30]
        for (code, mp, ma), st in preview:
            stars = _stars_by_rule(float(st["win_rate"]), int(st["n"]))
            self.stdout.write(
                f"  {code} [{mp}/{ma}]: n={st['n']:3d} win_rate={st['win_rate']:5.1f}% avg_pl={st['avg_pl']} -> stars={stars}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] dry-run のためDB更新は行いません。"))
            return

        # DBへ upsert
        with transaction.atomic():
            upserted = 0
            for (code, mp, ma), st in stats.items():
                win_rate = float(st["win_rate"])
                n = int(st["n"])
                stars = int(_stars_by_rule(win_rate, n))

                # もし BehaviorStats に broker フィールドが存在するなら "all" で固定保存
                lookup_kwargs = {"code": str(code), "mode_period": mp, "mode_aggr": ma}
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