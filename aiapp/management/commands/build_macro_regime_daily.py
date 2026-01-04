# -*- coding: utf-8 -*-
"""
aiapp.management.commands.build_macro_regime_daily

指数・為替などのベンチマーク価格をまとめて更新し、
その結果から MacroRegimeSnapshot を 1件（当日分）生成するバッチ。

想定フロー:
    ensure_benchmark_master()
    sync_benchmark_prices(days=365)
    snap = build_macro_regime_snapshot()
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from aiapp.services.macro_regime import (
    ensure_benchmark_master,
    sync_benchmark_prices,
    build_macro_regime_snapshot,
)


class Command(BaseCommand):
    help = (
        "ベンチマーク価格を更新し、当日分の MacroRegimeSnapshot を生成する。"
        "picks_build / scoring_service から参照される前提のデイリーバッチ。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=365,
            help="何日分の履歴を取得するか（デフォルト365日）。",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="ベンチマーク価格のダウンロードをスキップして、"
                 "既存データから Snapshot だけを再計算する。",
        )

    def handle(self, *args, **options):
        days: int = options["days"]
        do_sync: bool = not options["no_sync"]

        self.stdout.write(self.style.NOTICE("[macro_regime] ensure_benchmark_master() 開始"))
        ensure_benchmark_master()
        self.stdout.write(self.style.SUCCESS("[macro_regime] ensure_benchmark_master() 完了"))

        if do_sync:
            self.stdout.write(
                self.style.NOTICE(
                    f"[macro_regime] sync_benchmark_prices(days={days}) 開始"
                )
            )
            sync_benchmark_prices(days=days)
            self.stdout.write(self.style.SUCCESS("[macro_regime] sync_benchmark_prices() 完了"))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "[macro_regime] --no-sync 指定のため sync_benchmark_prices() をスキップ"
                )
            )

        self.stdout.write(self.style.NOTICE("[macro_regime] build_macro_regime_snapshot() 開始"))
        snap = build_macro_regime_snapshot()
        date_str = getattr(snap, "date", None)
        summary = getattr(snap, "summary", "") or ""

        self.stdout.write(
            self.style.SUCCESS(
                f"[macro_regime] build_macro_regime_snapshot() 完了 date={date_str} summary={summary}"
            )
        )