# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from ...models_ab import ABExperiment, ABEvent

class Command(BaseCommand):
    help = "A/B イベントの簡易日次レポート（昨日～今日）を出力します。"

    def add_arguments(self, parser):
        parser.add_argument("--key", default="ai_advisor_layout")
        parser.add_argument("--days", type=int, default=1)

    def handle(self, *args, **opts):
        key = opts["key"]
        days = int(opts["days"])
        since = timezone.now() - timedelta(days=days)

        exp = ABExperiment.objects.filter(key=key).first()
        if not exp:
            self.stdout.write(self.style.WARNING(f"experiment not found: {key}"))
            return

        evs = ABEvent.objects.filter(experiment=exp, at__gte=since)
        self.stdout.write(f"[{key}] events since {since:%Y-%m-%d %H:%M}")
        by_var = {}
        for e in evs:
            d = by_var.setdefault(e.variant or "?", {"view":0,"click_check":0,"gen_weekly":0,"gen_nextmove":0,"conversion":0,"_total":0})
            d[e.name] = d.get(e.name, 0) + 1
            d["_total"] += 1

        for v, s in by_var.items():
            views = max(1, s.get("view", 0))
            ctr = (s.get("click_check", 0) / views) * 100.0
            self.stdout.write(f" - {v}: views={s.get('view',0)} click_check={s.get('click_check',0)} CTR={ctr:.1f}% total={s.get('_total',0)}")