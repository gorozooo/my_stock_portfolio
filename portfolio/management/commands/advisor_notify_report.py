# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings

from ...services.advisor_metrics import weekly_notify_stats, current_thresholds, latest_week_summary

class Command(BaseCommand):
    help = "通知トレンドをJSONへ出力（可視化・外部ダッシュボード向け）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--out", type=str, default="media/advisor/notify_trend.json")

    def handle(self, *args, **opts):
        days = int(opts["days"])
        out_rel = opts["out"]
        base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
        out = Path(out_rel if os.path.isabs(out_rel) else os.path.join(base, out_rel))
        out.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "days": days,
            "stats": weekly_notify_stats(days),
            "thresholds": current_thresholds(),
            "headline": latest_week_summary(days),
        }
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        self.stdout.write(self.style.SUCCESS(f"Wrote {out}"))