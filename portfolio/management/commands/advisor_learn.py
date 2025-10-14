# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from datetime import timedelta
import json, tempfile, os

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from portfolio.models_advisor import AdviceItem


def _atomic_write(fp: Path, data: str) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    # 一時ファイル → rename で原子的に置換（途中読み込みを防止）
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(fp.parent)) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    os.replace(tmp_path, fp)


class Command(BaseCommand):
    help = "過去のアドバイス採用実績から policy.json（重みファイル）を自動生成する"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="学習に使う日数（過去N日） default=90",
        )
        parser.add_argument(
            "--out",
            type=str,
            default="media/advisor/policy.json",
            help="出力先パス（プロジェクトルートからの相対 or 絶対パス）",
        )
        parser.add_argument(
            "--bias",
            type=float,
            default=1.0,
            help="全体バイアス（倍率） default=1.0",
        )
        parser.add_argument(
            "--clip_low",
            type=float,
            default=0.80,
            help="重みの下限（クリップ） default=0.80",
        )
        parser.add_argument(
            "--clip_high",
            type=float,
            default=1.30,
            help="重みの上限（クリップ） default=1.30",
        )

    def handle(self, *args, **opts):
        days = int(opts["days"])
        out_path = Path(opts["out"])
        bias = float(opts["bias"])
        clip_low = float(opts["clip_low"])
        clip_high = float(opts["clip_high"])

        since = timezone.now() - timedelta(days=days)

        # 直近N日のアドバイス実績を取得
        qs = (
            AdviceItem.objects
            .filter(created_at__gte=since)
            .values("kind", "taken")
        )

        if not qs.exists():
            self.stdout.write(self.style.WARNING(
                f"No AdviceItem found in last {days} days. 出力はデフォルト重みになります。"
            ))

        # kind ごとに「提示件数」と「採用件数」を集計
        kinds = {}
        for row in qs:
            k = row["kind"] or "REBALANCE"
            taken = 1 if row["taken"] else 0
            acc = kinds.setdefault(k, {"n": 0, "taken": 0})
            acc["n"] += 1
            acc["taken"] += taken

        # ラプラス平滑化で (taken+1)/(n+2) → 採用率のラフな推定
        # さらに全 kind の平均が 1.0 になるように正規化
        raw_weight = {}
        for k, v in kinds.items():
            n = v["n"]
            t = v["taken"]
            p = (t + 1) / (n + 2) if n >= 0 else 0.5  # 0件でも0.5
            raw_weight[k] = p

        if not raw_weight:
            # 実績が全く無い場合はデフォルト種別だけ用意
            raw_weight = {
                "REBALANCE": 1.0,
                "ADD_CASH": 1.0,
                "TRIM_WINNERS": 1.0,
                "CUT_LOSERS": 1.0,
                "REDUCE_MARGIN": 1.0,
            }

        avg = sum(raw_weight.values()) / max(len(raw_weight), 1)
        normed = {k: (v / (avg or 1.0)) for k, v in raw_weight.items()}  # 平均1.0に正規化

        # クリップ & 全体バイアス乗算
        kind_weight = {
            k: max(clip_low, min(clip_high, bias * w))
            for k, w in normed.items()
        }

        # policy.json を作成
        payload = {
            "version": 1,
            "updated_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bias": bias,
            "kind_weight": kind_weight,
        }

        _atomic_write(out_path, json.dumps(payload, ensure_ascii=False, indent=2))
        self.stdout.write(self.style.SUCCESS(
            f"Wrote policy.json → {out_path}  (kinds={len(kind_weight)})"
        ))