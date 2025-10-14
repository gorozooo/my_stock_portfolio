# portfolio/management/commands/advisor_learn.py
from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils import timezone

from ...services.learner import learn_from_outcomes

class Command(BaseCommand):
    help = "AIアドバイザー: チェック済みアドバイスの成果を評価し、学習ポリシー(AdvicePolicy)を更新します。"

    def add_arguments(self, parser):
        parser.add_argument("--horizon", type=int, default=14,
                            help="評価期間（日数）。この日数より前の taken=True を学習対象にします（default: 14）")

    def handle(self, *args, **options):
        horizon = int(options["horizon"])
        updated = learn_from_outcomes(horizon_days=horizon)
        now = timezone.now().strftime("%Y-%m-%d %H:%M")
        if updated == 0:
            self.stdout.write(self.style.WARNING(f"[{now}] 学習対象なし（horizon={horizon}日）"))
        else:
            self.stdout.write(self.style.SUCCESS(f"[{now}] 学習更新 {updated} 件（horizon={horizon}日）"))