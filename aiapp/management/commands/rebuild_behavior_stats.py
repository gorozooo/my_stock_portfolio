# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from math import log
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from aiapp.models.behavior_stats import BehaviorStats


JST = dt_timezone(timedelta(hours=9))

# ✅ PRO一択
BROKERS = ("pro",)


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


def _to_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(JST).replace(tzinfo=None)
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except Exception:
        return None


def _clamp01(x: float) -> float:
    try:
        x = float(x)
    except Exception:
        return 0.5
    if not np.isfinite(x):
        return 0.5
    return float(min(1.0, max(0.0, x)))


def _sigmoid(x: float) -> float:
    try:
        return float(1.0 / (1.0 + np.exp(-float(x))))
    except Exception:
        return 0.5


def _sum_eval_pl_pro(d: Dict[str, Any]) -> Optional[float]:
    """
    ✅ PRO一択の eval_pl_pro を返す。
    古いデータにフィールドが無い場合は None を返す（上流で除外/フォールバック可能にする）。
    """
    v = _safe_float(d.get("eval_pl_pro"))
    if v is None:
        return None
    return float(v)


def _label_pro(d: Dict[str, Any]) -> Optional[str]:
    """
    ✅ PRO一択の eval_label_pro を返す。
    """
    v = d.get("eval_label_pro")
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def _extract_rr_design_hint(d: Dict[str, Any]) -> Optional[float]:
    rr = _safe_float(d.get("rr"))
    if rr is None:
        rr = _safe_float(d.get("RR"))
    if rr is not None and rr > 0:
        return float(rr)

    e = _safe_float(d.get("entry"))
    t = _safe_float(d.get("tp"))
    s = _safe_float(d.get("sl"))
    if e is None or t is None or s is None:
        return None
    reward = float(t) - float(e)
    risk = float(e) - float(s)
    if reward <= 0 or risk <= 0:
        return None
    return float(reward / risk)


def _compute_stability(
    *,
    n: int,
    win: int,
    lose: int,
    flat: int,
    avg_pl: Optional[float],
    std_pl: Optional[float],
) -> float:
    if n <= 0:
        return 0.50

    r = _sigmoid((n - 8) / 3.0)

    w = max(0, int(win))
    l = max(0, int(lose))
    f = max(0, int(flat))
    tot = w + l + f
    if tot <= 0:
        entropy_norm = 1.0
    else:
        ps = []
        for x in (w, l, f):
            if x > 0:
                ps.append(x / tot)
        ent = 0.0
        for p in ps:
            ent -= p * log(p)
        ent_max = log(3.0)
        entropy_norm = (ent / ent_max) if ent_max > 0 else 1.0
    stab_outcome = 1.0 - float(entropy_norm)

    ap = float(avg_pl) if (avg_pl is not None and np.isfinite(avg_pl)) else 0.0
    sp = float(std_pl) if (std_pl is not None and np.isfinite(std_pl)) else None

    if sp is None:
        stab_pl = 0.50
    else:
        scale = max(1200.0, 0.5 * abs(ap) + 1200.0)
        stab_pl = 1.0 / (1.0 + (sp / scale))
        stab_pl = _clamp01(stab_pl)

    base = 0.60 * stab_outcome + 0.40 * stab_pl
    base = _clamp01(base)

    blended = (1.0 - r) * 0.50 + r * base
    return _clamp01(blended)


def _compute_design_q(
    *,
    n: int,
    avg_pl: Optional[float],
    rr_vals: List[float],
) -> float:
    if n <= 0:
        return 0.50

    r_gate = _sigmoid((n - 8) / 3.0)

    rr_score = None
    vals = [float(x) for x in rr_vals if x is not None and np.isfinite(x) and x > 0]
    if vals:
        rr_med = float(np.median(vals))
        rr_score = _sigmoid((rr_med - 1.0) * 2.2)
        rr_score = _clamp01(rr_score)

    if rr_score is None:
        base = 0.50
    else:
        base = rr_score

    ap = float(avg_pl) if (avg_pl is not None and np.isfinite(avg_pl)) else 0.0
    pl_adj = _sigmoid(ap / 3000.0)
    base2 = _clamp01(0.80 * base + 0.20 * pl_adj)

    blended = (1.0 - r_gate) * 0.50 + r_gate * base2
    return _clamp01(blended)


@dataclass
class Rec:
    code: str
    mode_period: str
    mode_aggr: str
    source: str
    eval_label: Optional[str]
    eval_pl: Optional[float]
    run_date: Optional[datetime]
    rr_hint: Optional[float]


def _load_latest_behavior_jsonl(
    *,
    days: int,
    include_live: bool = False,
) -> List[Rec]:
    """
    media/aiapp/behavior/latest_behavior.jsonl から、直近days日を読む。

    ✅ PRO一択:
      - label: eval_label_pro
      - pl   : eval_pl_pro
    """
    behavior_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "behavior"
    latest_path = behavior_dir / "latest_behavior.jsonl"
    if not latest_path.exists():
        return []

    now = datetime.now(JST).replace(tzinfo=None)
    cutoff = now - timedelta(days=int(days))

    out: List[Rec] = []

    try:
        text = latest_path.read_text(encoding="utf-8")
    except Exception:
        return []

    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue

        src = str(d.get("source") or "").strip().lower()
        mode = str(d.get("mode") or "").strip().lower()

        if not include_live and mode == "live":
            continue

        run_date = _to_date(str(d.get("run_date") or "") or None) or _to_date(str(d.get("ts") or "") or None)
        if run_date is not None and run_date < cutoff:
            continue

        code = str(d.get("code") or "").strip()
        if not code:
            continue
        if code.endswith(".T"):
            code = code[:-2]

        label = _label_pro(d)
        plv = _sum_eval_pl_pro(d)

        # ✅ PROフィールドが無い古いデータはスキップ（混入しても落ちないように）
        if label is None and plv is None:
            continue

        rr_hint = _extract_rr_design_hint(d)

        out.append(
            Rec(
                code=code,
                mode_period="all",
                mode_aggr="all",
                source=src,
                eval_label=label,
                eval_pl=plv,
                run_date=run_date,
                rr_hint=rr_hint,
            )
        )

    return out


def _stars_rule(win_rate_pct: float, n: int, avg_pl: Optional[float]) -> int:
    if n < 5:
        return 1

    wr = win_rate_pct

    if avg_pl is not None and avg_pl < -3000:
        if wr >= 60:
            return 3
        if wr >= 50:
            return 2
        return 1

    if wr >= 70:
        return 5
    if wr >= 60:
        return 4
    if wr >= 50:
        return 3
    if wr >= 45:
        return 2
    return 1


class Command(BaseCommand):
    help = "BehaviorStats を再集計してDBへ upsert（紙シミュ育成: all/all・PRO一択）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--include-live", action="store_true", help="LIVE も統合に含める（基本はOFF）")
        parser.add_argument("--dry-run", action="store_true", help="DB更新せずプレビューだけ")
        parser.add_argument("--cleanup-zero", action="store_true", help="n=0 の既存行を掃除してから upsert")
        # 互換オプション（昔の呼び方でも落とさない）
        parser.add_argument("--broker", type=str, default=None)
        parser.add_argument("--mode_period", type=str, default=None)
        parser.add_argument("--mode_aggr", type=str, default=None)

    def handle(self, *args, **opts) -> None:
        days = int(opts.get("days") or 90)
        include_live = bool(opts.get("include_live") or False)
        dry_run = bool(opts.get("dry_run") or False)
        cleanup_zero = bool(opts.get("cleanup_zero") or False)

        recs = _load_latest_behavior_jsonl(days=days, include_live=include_live)

        if not recs:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] 対象レコードがありません。"))
            return

        bucket: Dict[str, Dict[str, Any]] = {}
        for r in recs:
            b = bucket.setdefault(
                r.code,
                {"n": 0, "win": 0, "lose": 0, "flat": 0, "pls": [], "rrs": []},
            )

            if r.eval_label in ("win", "lose", "flat"):
                b["n"] += 1
                if r.eval_label == "win":
                    b["win"] += 1
                elif r.eval_label == "lose":
                    b["lose"] += 1
                else:
                    b["flat"] += 1

                if r.eval_pl is not None:
                    b["pls"].append(float(r.eval_pl))
                if r.rr_hint is not None and np.isfinite(float(r.rr_hint)) and float(r.rr_hint) > 0:
                    b["rrs"].append(float(r.rr_hint))

        bucket = {code: st for code, st in bucket.items() if int(st.get("n") or 0) > 0}
        unique_codes = len(bucket)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("===== rebuild_behavior_stats preview (PRO only) ====="))
        self.stdout.write(f"  days={days}  include_live={include_live}  dry_run={dry_run}  cleanup_zero={cleanup_zero}")
        self.stdout.write("  broker=pro (only)")
        self.stdout.write(f"  unique_codes(n>0)={unique_codes}")

        if unique_codes == 0:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] n>0 の銘柄がありません。"))
            return

        def _avg(xs: List[float]) -> Optional[float]:
            if not xs:
                return None
            return float(np.mean(xs))

        def _std(xs: List[float]) -> Optional[float]:
            if not xs or len(xs) < 2:
                return None
            return float(np.std(xs, ddof=0))

        preview_rows: List[Tuple[str, int, float, Optional[float], Optional[float], float, float, int]] = []
        for code, st in bucket.items():
            n = int(st["n"])
            win = int(st["win"])
            lose = int(st["lose"])
            flat = int(st["flat"])

            wr = (100.0 * win / n) if n > 0 else 0.0
            avg_pl = _avg(st["pls"])
            std_pl = _std(st["pls"])

            stability = _compute_stability(n=n, win=win, lose=lose, flat=flat, avg_pl=avg_pl, std_pl=std_pl)
            design_q = _compute_design_q(n=n, avg_pl=avg_pl, rr_vals=st.get("rrs") or [])
            stars = _stars_rule(wr, n, avg_pl)

            preview_rows.append((code, n, wr, avg_pl, std_pl, stability, design_q, stars))

        preview_rows.sort(key=lambda x: x[1], reverse=True)

        for code, n, wr, avg_pl, std_pl, stability, design_q, stars in preview_rows[:30]:
            ap = 0.0 if avg_pl is None else avg_pl
            self.stdout.write(
                f"  {code} [all/all]: n={n:3d} win_rate={wr:5.1f}% avg_pl={ap:7.1f} "
                f"stability={float(stability):.2f} design_q={float(design_q):.2f} -> stars={stars}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("[rebuild_behavior_stats] dry-run のため DB 更新は行いません。"))
            return

        now = timezone.now()
        upserted = 0

        with transaction.atomic():
            if cleanup_zero:
                deleted, _ = BehaviorStats.objects.filter(mode_period="all", mode_aggr="all", n=0).delete()
                self.stdout.write(self.style.WARNING(f"[rebuild_behavior_stats] cleanup_zero: deleted={deleted}"))

            for code, n, wr, avg_pl, std_pl, stability, design_q, stars in preview_rows:
                win = int(bucket[code]["win"])
                lose = int(bucket[code]["lose"])
                flat = int(bucket[code]["flat"])

                BehaviorStats.objects.update_or_create(
                    code=str(code),
                    mode_period="all",
                    mode_aggr="all",
                    defaults={
                        "stars": int(stars),
                        "n": int(n),
                        "win": int(win),
                        "lose": int(lose),
                        "flat": int(flat),
                        "win_rate": float(round(wr, 1)),
                        "avg_pl": float(avg_pl) if avg_pl is not None else None,
                        "std_pl": float(std_pl) if std_pl is not None else None,
                        "stability": float(_clamp01(stability)),
                        "design_q": float(_clamp01(design_q)),
                        "window_days": int(days),
                        "updated_at": now,
                    },
                )
                upserted += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[rebuild_behavior_stats] DB更新完了: {upserted} 件 upsert（n>0のみ）"))