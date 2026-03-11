"""
[FILE] shihyo_run_preopen_pipeline.py
[PATH] <project_root>/shihyo/management/commands/shihyo_run_preopen_pipeline.py

このファイルは何？
- 朝7:00用の shihyo パイプラインを1コマンドで実行する orchestration command です。
- 実行順は以下です。
  1) shihyo_fetch
  2) shihyo_build_market_bias_preopen
  3) shihyo_build_preopen_features
  4) shihyo_predict_preopen
- これにより、朝の必要データ取得から、
  朝7時モデルの特徴量保存・予測保存までを一気に流せます。

使い方:
  python manage.py shihyo_run_preopen_pipeline
  python manage.py shihyo_run_preopen_pipeline --trade-date 2026-03-11

補足:
- 週次レビューはこのパイプラインには入れていません。
- 週次レビューは引け後に実績確定が進んでから別で回す前提です。
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
    help = "朝7:00用の shihyo パイプラインを順番に実行します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--trade-date",
            type=str,
            default="",
            help="対象営業日(YYYY-MM-DD)。未指定ならJSTの今日。",
        )
        parser.add_argument(
            "--feature-version",
            type=str,
            default="preopen_feature_v1",
            help="特徴量保存時の feature_version。",
        )
        parser.add_argument(
            "--model-version",
            type=str,
            default="rule_v1",
            help="予測保存時の model_version。",
        )
        parser.add_argument(
            "--skip-fetch",
            action="store_true",
            help="shihyo_fetch をスキップする。",
        )
        parser.add_argument(
            "--skip-preopen-bias",
            action="store_true",
            help="shihyo_build_market_bias_preopen をスキップする。",
        )
        parser.add_argument(
            "--skip-features",
            action="store_true",
            help="shihyo_build_preopen_features をスキップする。",
        )
        parser.add_argument(
            "--skip-predict",
            action="store_true",
            help="shihyo_predict_preopen をスキップする。",
        )

    def handle(self, *args, **options):
        trade_date_str = (options.get("trade_date") or "").strip()
        feature_version = str(options.get("feature_version") or "preopen_feature_v1").strip()
        model_version = str(options.get("model_version") or "rule_v1").strip()

        trade_date = timezone.localdate()
        if trade_date_str:
            trade_date = _parse_date(trade_date_str)

        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_run_preopen_pipeline] start "
                f"trade_date={trade_date.isoformat()} "
                f"feature_version={feature_version} "
                f"model_version={model_version}"
            )
        )

        # 1) 指標取得
        if options.get("skip_fetch"):
            self.stdout.write("[1/4] shihyo_fetch (skipped)")
        else:
            self.stdout.write("[1/4] shihyo_fetch")
            call_command("shihyo_fetch")

        # 2) 朝予報の市場の偏り
        if options.get("skip_preopen_bias"):
            self.stdout.write("[2/4] shihyo_build_market_bias_preopen (skipped)")
        else:
            self.stdout.write("[2/4] shihyo_build_market_bias_preopen")
            call_command("shihyo_build_market_bias_preopen")

        # 3) 特徴量保存
        if options.get("skip_features"):
            self.stdout.write("[3/4] shihyo_build_preopen_features (skipped)")
        else:
            self.stdout.write("[3/4] shihyo_build_preopen_features")
            call_command(
                "shihyo_build_preopen_features",
                trade_date=trade_date.isoformat(),
                feature_version=feature_version,
            )

        # 4) 予測保存
        if options.get("skip_predict"):
            self.stdout.write("[4/4] shihyo_predict_preopen (skipped)")
        else:
            self.stdout.write("[4/4] shihyo_predict_preopen")
            call_command(
                "shihyo_predict_preopen",
                trade_date=trade_date.isoformat(),
                model_version=model_version,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"[shihyo_run_preopen_pipeline] done "
                f"trade_date={trade_date.isoformat()} "
                f"feature_version={feature_version} "
                f"model_version={model_version}"
            )
        )