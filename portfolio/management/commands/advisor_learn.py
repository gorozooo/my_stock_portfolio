# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from datetime import timedelta
import json, tempfile, os

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.conf import settings

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
            help="出力先パス（プロジェクトルートからの相対 or 絶対パス）。相対の場合は MEDIA_ROOT を起点に解決",
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
        parser.add_argument(
            "--print",
            action="store_true",
            help="生成した policy を標準出力に表示",
        )

    def handle(self, *args, **opts):
        days = int(opts["days"])
        out_arg = str(opts["out"])
        bias = float(opts["bias"])
        clip_low = float(opts["clip_low"])
        clip_high = float(opts["clip_high"])
        do_print = bool(opts["print"])

        # 出力先パス解決（相対なら MEDIA_ROOT 起点）
        if os.path.isabs(out_arg):
            out_path = Path(out_arg)
        else:
            base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
            out_path = Path(base) / out_arg

        since = timezone.now() - timedelta(days=days)

        # 直近N日のアドバイス実績を取得
        qs = (
            AdviceItem.objects
            .filter(created_at__gte=since)
            .values("kind", "taken")
        )

        # kind ごとに「提示件数」と「採用件数」を集計
        kinds: dict[str, dict[str, int]] = {}
        for row in qs:
            k = row["kind"] or "REBALANCE"
            taken = 1 if row["taken"] else 0
            acc = kinds.setdefault(k, {"n": 0, "taken": 0})
            acc["n"] += 1
            acc["taken"] += taken

        # 実績ゼロの場合の既定セット（可読性のため代表的な種類を含める）
        if not kinds:
            self.stdout.write(self.style.WARNING(
                f"No AdviceItem found in last {days} days. 出力はデフォルト重みになります。"
            ))
            kinds = {
                "REBALANCE": {"n": 0, "taken": 0},
                "ADD_CASH": {"n": 0, "taken": 0},
                "TRIM_WINNERS": {"n": 0, "taken": 0},
                "CUT_LOSERS": {"n": 0, "taken": 0},
                "REDUCE_MARGIN": {"n": 0, "taken": 0},
            }

        # ラプラス平滑化で (taken+1)/(n+2) → 採用率のラフな推定
        raw_weight: dict[str, float] = {}
        for k, v in kinds.items():
            n = int(v.get("n", 0))
            t = int(v.get("taken", 0))
            p = (t + 1) / (n + 2) if n >= 0 else 0.5  # 0件でも0.5
            raw_weight[k] = float(p)

        # 平均が 1.0 になるように正規化
        avg = sum(raw_weight.values()) / max(len(raw_weight), 1)
        avg = avg or 1.0
        normed = {k: (v / avg) for k, v in raw_weight.items()}

        # クリップ & 全体バイアス乗算
        kind_weight = {
            k: max(clip_low, min(clip_high, bias * w))
            for k, w in normed.items()
        }

        # サマリ（可視化用に counts も保存）
        total_items = sum(int(v["n"]) for v in kinds.values())
        counts = {k: int(v["n"]) for k, v in kinds.items()}

        payload = {
            "version": 1,
            "updated_at": timezone.now().isoformat(),
            "window_days": days,
            "bias": bias,
            "clip": {"low": clip_low, "high": clip_high},
            "summary": {"total_items": total_items, "counts": counts},
            "kind_weight": kind_weight,
        }

        # 書き込み（本体）
        _atomic_write(out_path, json.dumps(payload, ensure_ascii=False, indent=2))

        # 履歴コピー（policy_YYYY-MM-DD.json）
        try:
            ts = payload["updated_at"][:10]  # YYYY-MM-DD
            hist_dir = out_path.parent / "history"
            hist_dir.mkdir(parents=True, exist_ok=True)
            hist_path = hist_dir / f"policy_{ts}.json"
            _atomic_write(hist_path, json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception as e:
            # 履歴失敗は致命的ではないので警告のみ
            self.stdout.write(self.style.WARNING(f"history write failed: {e}"))

        if do_print:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))

        self.stdout.write(self.style.SUCCESS(
            f"Wrote policy.json → {out_path}  (kinds={len(kind_weight)})"
        ))