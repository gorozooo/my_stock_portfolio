# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from datetime import timedelta
from typing import Dict, List, Optional

from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from ...models_advisor import AdviceSession

# 既存の学習ロジックに合わせた簡易アウトカム推定
def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _improve_between(k0: Dict, k1: Dict) -> float:
    if not k0 or not k1:
        return 0.0
    d_roi = _safe_float(k1.get("roi_eval_pct")) - _safe_float(k0.get("roi_eval_pct"))
    d_liq = _safe_float(k1.get("liquidity_rate_pct")) - _safe_float(k0.get("liquidity_rate_pct"))
    d_mrg = _safe_float(k0.get("margin_ratio_pct")) - _safe_float(k1.get("margin_ratio_pct"))
    def clip(x, s): return max(-1.0, min(1.0, x / s)) if s else 0.0
    return (clip(d_roi, 50.0) + clip(d_liq, 40.0) + clip(d_mrg, 40.0)) / 3.0

class Command(BaseCommand):
    help = "A/B 実験の簡易レポート（採用率・改善スコア）を出力"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--horizon", type=int, default=7)
        parser.add_argument("--since-days", type=int, default=90)
        parser.add_argument("--print", action="store_true")

    def handle(self, *args, **opts):
        horizon = int(opts["horizon"])
        since_days = int(opts["since_days"])
        cutoff = timezone.now() - timedelta(days=since_days)

        sessions = list(AdviceSession.objects.filter(created_at__gte=cutoff).order_by("created_at"))

        stats = {
            "A": {"sessions": 0, "advice": 0, "taken": 0, "improve_sum": 0.0, "improve_n": 0},
            "B": {"sessions": 0, "advice": 0, "taken": 0, "improve_sum": 0.0, "improve_n": 0},
        }

        # horizon 後のセッションを探す
        def find_future(idx: int):
            base = sessions[idx]
            target = base.created_at + timedelta(days=horizon)
            for j in range(idx + 1, len(sessions)):
                if sessions[j].created_at >= target:
                    return sessions[j]
            return None

        for i, s in enumerate(sessions):
            v = (s.variant or "A").upper()
            if v not in stats:
                continue
            stats[v]["sessions"] += 1
            items = list(s.items.all())
            stats[v]["advice"] += len(items)
            stats[v]["taken"] += sum(1 for it in items if it.taken)

            fut = find_future(i)
            if fut:
                sc = _improve_between(s.context_json or {}, fut.context_json or {})
                stats[v]["improve_sum"] += float(sc)
                stats[v]["improve_n"] += 1

        for v in ("A","B"):
            d = stats[v]
            d["take_rate"] = (d["taken"] / d["advice"]) if d["advice"] > 0 else 0.0
            d["avg_improve"] = (d["improve_sum"] / d["improve_n"]) if d["improve_n"] > 0 else 0.0

        out = {"horizon": horizon, "since_days": since_days, "stats": stats}
        if opts["print"]:
            self.stdout.write(json.dumps(out, ensure_ascii=False, indent=2))
        self.stdout.write(self.style.SUCCESS("[advisor_ab_report] done"))