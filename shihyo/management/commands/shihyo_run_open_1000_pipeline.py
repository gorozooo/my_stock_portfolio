"""
[FILE] shihyo_run_open_1000_pipeline.py
[PATH] <project_root>/shihyo/management/commands/shihyo_run_open_1000_pipeline.py

このファイルは何？
- 10:00用の shihyo パイプラインを1コマンドで実行する orchestration command です。
- 実行順は以下です。
  1) shihyo_fetch
  2) shihyo_load_intraday_prices
  3) shihyo_build_market_bias_open_1000
  4) shihyo_predict_open_1000

これにより、
- 10:00時点の市場の偏り更新
- 10:00再予想の保存
までを一気に流せます。

今回の修正ポイント：
- shihyo_load_intraday_prices へ渡す引数名を
  trade_date ではなく date に修正
- 実コマンドの受け口に合わせる
"""

from __future__ import annotations

from datetime import date

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone


def _parse_date(value: str) -> date:
    y, m, d = [int(x) for x in value.split("-")]
    return date(y, m, d)


class Command(BaseCommand):
    help = "10:00用の shihyo パイプラインを順番に実行します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--trade-date",
            type=str,
            default="",
            help="対象営業日(YYYY-MM-DD)。未指定ならJSTの今日。",
        )
        parser.add_argument(
            "--model-version",
            type=str,
            default="rule_open1000_v1",
            help="10:00再予想の model_version。",
        )
        parser.add_argument(
            "--feature-version",
            type=str,
            default="open1000_feature_v1",
            help="10:00再予想の feature_version。",
        )
        parser.add_argument(
            "--skip-fetch",
            action="store_true",
            help="shihyo_fetch をスキップする。",
        )
        parser.add_argument(
            "--skip-load-intraday",
            action="store_true",
            help="shihyo_load_intraday_prices をスキップする。",
        )
        parser.add_argument(
            "--skip-open-bias",
            action="store_true",
            help="shihyo_build_market_bias_open_1000 をスキップする。",
        )
        parser.add_argument(
            "--skip-predict",
            action="store_true",
            help="shihyo_predict_open_1000 をスキップする。",
        )

    def handle(self, *args, **options):
        trade_date_str = (options.get("trade_date") or "").strip()
        model_version = str(options.get("model_version") or "rule_open1000_v1").strip()
        feature_version = str(options.get("feature_version") or "open1000_feature_v1").strip()

        trade_date = timezone.localdate()
        if trade_date_str:
            trade_date = _parse_date(trade_date_str)

        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_run_open_1000_pipeline] start "
                f"trade_date={trade_date.isoformat()} "
                f"feature_version={feature_version} "
                f"model_version={model_version}"
            )
        )

        if options.get("skip_fetch"):
            self.stdout.write("[1/4] shihyo_fetch (skipped)")
        else:
            self.stdout.write("[1/4] shihyo_fetch")
            call_command("shihyo_fetch")

        if options.get("skip_load_intraday"):
            self.stdout.write("[2/4] shihyo_load_intraday_prices (skipped)")
        else:
            self.stdout.write("[2/4] shihyo_load_intraday_prices")
            call_command(
                "shihyo_load_intraday_prices",
                date=trade_date.isoformat(),
            )

        if options.get("skip_open_bias"):
            self.stdout.write("[3/4] shihyo_build_market_bias_open_1000 (skipped)")
        else:
            self.stdout.write("[3/4] shihyo_build_market_bias_open_1000")
            call_command("shihyo_build_market_bias_open_1000")

        if options.get("skip_predict"):
            self.stdout.write("[4/4] shihyo_predict_open_1000 (skipped)")
        else:
            self.stdout.write("[4/4] shihyo_predict_open_1000")
            call_command(
                "shihyo_predict_open_1000",
                trade_date=trade_date.isoformat(),
                model_version=model_version,
                feature_version=feature_version,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_run_open_1000_pipeline] done "
                f"trade_date={trade_date.isoformat()} "
                f"feature_version={feature_version} "
                f"model_version={model_version}"
            )
        )