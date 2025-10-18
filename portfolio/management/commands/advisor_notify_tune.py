# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, tempfile
from pathlib import Path
from datetime import timedelta
from typing import Dict

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from ...models_advisor import AdviceItem

# ---- 小ヘルパ ----
def _atomic_write(fp: Path, data: str) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(fp.parent)) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    os.replace(tmp_path, fp)

def _policy_path() -> Path:
    rel = getattr(settings, "ADVISOR_POLICY_PATH", "media/advisor/policy.json")
    base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    p = Path(rel) if os.path.isabs(rel) else Path(base) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _load_policy() -> Dict:
    pp = _policy_path()
    if pp.exists():
        try:
            return json.loads(pp.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_policy(blob: Dict) -> None:
    _atomic_write(_policy_path(), json.dumps(blob, ensure_ascii=False, indent=2))

# AdviceItem.message を簡易カテゴリ分け
CATS = {
    "GAP":      ["乖離"],
    "LIQ":      ["流動性"],
    "MARGIN":   ["信用比率"],
    "SECTOR":   ["セクター偏在"],
    "UNCAT":    ["未分類セクター比率"],
    "RS_WEAK":  ["相対強弱が弱気"],
    "RS_STRONG":["相対強弱が強気"],
    "BREADTH_B":"地合いが弱い（ブレッドス判定"],
    "BREADTH_G":"地合いが良好（ブレッドス判定"],
}

def _cat_of(msg: str) -> str:
    m = msg or ""
    for k, keys in CATS.items():
        if any(s in m for s in keys):
            return k
    return "OTHER"

def _default_notify_thresholds() -> Dict[str, float]:
    return {
        "gap_min": 20.0,
        "liq_max": 50.0,
        "margin_min": 60.0,
        "top_share_max": 45.0,
        "uncat_share_max": 40.0,
        "breadth_bad": -0.35,
        "breadth_good": 0.35,
    }

class Command(BaseCommand):
    help = "通知のしきい値を、直近の通知頻度に合わせて自動調整（多すぎ→厳しく / 少なすぎ→緩く）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=60, help="過去N日を学習窓にする")
        parser.add_argument("--target-weekly", type=float, default=10.0,
                            help="目標の“通知/週”総量（全カテゴリ合計）")
        parser.add_argument("--step", type=float, default=2.0,
                            help="%系しきい値の増減幅（breadthは自動で±0.05）")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        days = int(opts["days"])
        target_weekly = float(opts["target_weekly"])
        step = float(opts["step"])
        dry = bool(opts["dry_run"])

        since = timezone.now() - timedelta(days=days)
        qs = AdviceItem.objects.filter(created_at__gte=since)
        total = qs.count()
        weeks = max(days / 7.0, 1.0)
        per_week = total / weeks

        # 現状集計（カテゴリ別）
        cat_cnt: Dict[str, int] = {}
        for it in qs.only("message"):
            c = _cat_of(it.message)
            cat_cnt[c] = cat_cnt.get(c, 0) + 1

        pol = _load_policy()
        nt = (pol.get("notify_thresholds") or {}) or _default_notify_thresholds()

        direction = "tighten" if per_week > target_weekly * 1.10 else "loosen" if per_week < target_weekly * 0.90 else "keep"

        # %系の増減ルール
        def inc(x: float, dv: float) -> float: return round(x + dv, 2)
        def dec(x: float, dv: float) -> float: return round(x - dv, 2)

        before = dict(nt)

        if direction == "tighten":
            # 通知が多すぎる → トリガーを“厳しく”する
            nt["gap_min"]        = inc(nt["gap_min"], step)
            nt["liq_max"]        = dec(nt["liq_max"], step)
            nt["margin_min"]     = inc(nt["margin_min"], step)
            nt["top_share_max"]  = inc(nt["top_share_max"], step)
            nt["uncat_share_max"]= inc(nt["uncat_share_max"], step)
            nt["breadth_bad"]    = dec(nt["breadth_bad"], 0.05)   # -0.35 → -0.40（悪化をより厳しく）
            nt["breadth_good"]   = inc(nt["breadth_good"], 0.05)  #  0.35 →  0.40（好転をより厳しく）
        elif direction == "loosen":
            # 通知が少なすぎる → トリガーを“緩く”する
            nt["gap_min"]        = dec(nt["gap_min"], step)
            nt["liq_max"]        = inc(nt["liq_max"], step)
            nt["margin_min"]     = dec(nt["margin_min"], step)
            nt["top_share_max"]  = dec(nt["top_share_max"], step)
            nt["uncat_share_max"]= dec(nt["uncat_share_max"], step)
            nt["breadth_bad"]    = inc(nt["breadth_bad"], 0.05)   # -0.35 → -0.30
            nt["breadth_good"]   = dec(nt["breadth_good"], 0.05)  #  0.35 →  0.30

        # クリップ＆常識的範囲
        def clip(x, lo, hi): return float(max(lo, min(hi, x)))
        nt["gap_min"]         = clip(nt["gap_min"], 5, 80)
        nt["liq_max"]         = clip(nt["liq_max"], 10, 95)
        nt["margin_min"]      = clip(nt["margin_min"], 10, 95)
        nt["top_share_max"]   = clip(nt["top_share_max"], 20, 90)
        nt["uncat_share_max"] = clip(nt["uncat_share_max"], 10, 90)
        nt["breadth_bad"]     = clip(nt["breadth_bad"], -0.80, -0.10)
        nt["breadth_good"]    = clip(nt["breadth_good"], 0.10, 0.80)

        pol.setdefault("version", 2)
        pol.setdefault("updated_at", timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        pol["notify_thresholds"] = nt

        delta = {k: (before.get(k), nt.get(k)) for k in nt.keys() if before.get(k) != nt.get(k)}

        if dry:
            self.stdout.write(self.style.NOTICE(
                f"[DRY-RUN] direction={direction} per_week={per_week:.2f} target={target_weekly:.2f} delta={delta}"
            ))
        else:
            _save_policy(pol)
            self.stdout.write(self.style.SUCCESS(
                f"Updated notify_thresholds (direction={direction}, per_week={per_week:.2f}→target={target_weekly:.2f})"
            ))