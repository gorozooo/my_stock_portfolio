"""
[FILE] shihyo_run_pipeline.py
[PATH] <project_root>/shihyo/management/commands/shihyo_run_pipeline.py

このファイルは何？
- shihyo の自動更新パイプラインを、phase 単位でまとめて実行するコマンドです。
- cron からはこの1本を呼ぶだけでよくなるようにします。

対応 phase:
- preopen
    1) shihyo_fetch
    2) shihyo_build_market_bias_preopen

- open_1000
    1) shihyo_load_intraday_prices
    2) shihyo_build_market_bias_open_1000

使い方:
- 朝予報の更新
  python manage.py shihyo_run_pipeline --phase preopen

- 10:00実績の更新
  python manage.py shihyo_run_pipeline --phase open_1000

- 日付指定
  python manage.py shihyo_run_pipeline --phase open_1000 --date 2026-03-09

- テスト用に銘柄数を絞る
  python manage.py shihyo_run_pipeline --phase open_1000 --limit 20
"""

from __future__ import annotations

from django.core.management import BaseCommand, CommandError, call_command


class Command(BaseCommand):
    help = "shihyo の自動更新パイプラインを phase 単位で実行します。"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--phase",
            type=str,
            required=True,
            choices=["preopen", "open_1000"],
            help="実行する phase を指定します。",
        )
        parser.add_argument(
            "--date",
            type=str,
            default="",
            help="対象日 (YYYY-MM-DD)。open_1000 で利用。",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="テスト用。open_1000 で先頭から N 銘柄だけ取得。",
        )
        parser.add_argument(
            "--codes",
            type=str,
            default="",
            help="カンマ区切りで特定コードだけ取得。open_1000 で利用。",
        )
        parser.add_argument(
            "--chunk",
            type=int,
            default=50,
            help="yfinance へ投げる同時取得件数。open_1000 で利用。",
        )
        parser.add_argument(
            "--include-etf",
            action="store_true",
            help="open_1000 で ETF/ETN も含めて取得する。",
        )

    def handle(self, *args, **options):
        phase = str(options.get("phase") or "").strip()
        target_date = str(options.get("date") or "").strip()
        limit = int(options.get("limit") or 0)
        codes = str(options.get("codes") or "").strip()
        chunk = int(options.get("chunk") or 50)
        include_etf = bool(options.get("include_etf"))

        self.stdout.write(self.style.SUCCESS(f"[shihyo_run_pipeline] start phase={phase}"))

        if phase == "preopen":
            self.stdout.write("[1/2] shihyo_fetch")
            call_command("shihyo_fetch")

            self.stdout.write("[2/2] shihyo_build_market_bias_preopen")
            call_command("shihyo_build_market_bias_preopen")

            self.stdout.write(self.style.SUCCESS("[shihyo_run_pipeline] done phase=preopen"))
            return

        if phase == "open_1000":
            load_kwargs = {}
            build_kwargs = {}

            if target_date:
                load_kwargs["date"] = target_date
                build_kwargs["date"] = target_date
            if limit > 0:
                load_kwargs["limit"] = limit
            if codes:
                load_kwargs["codes"] = codes
            if chunk > 0:
                load_kwargs["chunk"] = chunk
            if include_etf:
                load_kwargs["include_etf"] = True

            self.stdout.write("[1/2] shihyo_load_intraday_prices")
            call_command("shihyo_load_intraday_prices", **load_kwargs)

            self.stdout.write("[2/2] shihyo_build_market_bias_open_1000")
            call_command("shihyo_build_market_bias_open_1000", **build_kwargs)

            self.stdout.write(self.style.SUCCESS("[shihyo_run_pipeline] done phase=open_1000"))
            return

        raise CommandError(f"unknown phase: {phase}")