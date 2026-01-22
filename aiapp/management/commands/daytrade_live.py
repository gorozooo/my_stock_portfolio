# -*- coding: utf-8 -*-
"""
ファイル: aiapp/management/commands/daytrade_live.py

これは何？
- VPSのcronから呼ぶ「本番デイトレ起動コマンド」。
- 今日の Judge snapshot が GO のときだけ、場中ループを開始する。
- NO_GOなら即終了（何もしない）。

置き場所
- <project_root>/aiapp/management/commands/daytrade_live.py

使い方
- python manage.py daytrade_live
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from datetime import date

from aiapp.services.daytrade.live_app import (
    DaytradeLiveApp,
    DummyRealtimeProvider,
    DummySignalProvider5m,
    is_go_today,
)


class Command(BaseCommand):
    help = "Run daytrade live (GO only)."

    def handle(self, *args, **options):
        today = date.today()

        if not is_go_today(today):
            self.stdout.write(self.style.WARNING("[DAYTRADE] NO_GO (skip live)"))
            return

        self.stdout.write(self.style.SUCCESS("[DAYTRADE] GO (start live)"))

        # 本番ではここを差し替える：
        # - realtime provider: 楽天RSS等
        # - signal provider: 5分足strategyから生成
        realtime = DummyRealtimeProvider()
        signal5m = DummySignalProvider5m()

        app = DaytradeLiveApp(realtime=realtime, signal5m=signal5m)
        app.run()