# aiapp/management/commands/train_behavior_model.py
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone


Number = Optional[float]


@dataclass
class Sample:
    """
    学習用の 1 レコード（片側：楽天 or 松井）
    """
    user_id: Optional[int]
    mode: str           # "live" / "demo" / "other"
    broker: str         # "rakuten" / "matsui" など
    sector: str         # 33業種 or "(未分類)"
    trend: str          # "up" / "flat" / "down" / "不明"
    time_bucket: str    # 時間帯バケット
    atr_bucket: str     # ATR バケット
    slope_bucket: str   # 傾きバケット
    y: int              # 1 = win, 0 = lose


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _bucket_time_of_day(ts_str: str) -> str:
    """
    ts 文字列から「時間帯バケット」を作る。
    behavior_dashboard 側と同じロジック。
    """
    if not ts_str:
        return "時間外/その他"
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        dt = timezone.localtime(dt)
    except Exception:
        return "時間外/その他"

    h = dt.hour * 60 + dt.minute
    if 9 * 60 <= h < 11 * 60 + 30:
        return "前場寄り〜11:30"
    if 11 * 60 + 30 <= h < 13 * 60:
        return "お昼〜後場寄り"
    if 13 * 60 <= h <= 15 * 60:
        return "後場〜大引け"
    return "時間外/その他"


def _bucket_atr(atr: Optional[float]) -> str:
    if atr is None:
        return "不明"
    if atr < 1.0:
        return "ATR 〜1%"
    if atr < 2.0:
        return "ATR 1〜2%"
    if atr < 3.0:
        return "ATR 2〜3%"
    return "ATR 3%以上"


def _bucket_slope(slope: Optional[float]) -> str:
    if slope is None:
        return "不明"
    if slope < 0:
        return "下向き"
    if slope < 5:
        return "緩やかな上向き"
    if slope < 10:
        return "強めの上向き"
    return "急騰寄り"


def _sigmoid(x: float) -> float:
    # 数値発散を避けるための簡易クリップ
    if x < -50:
        return 0.0
    if x > 50:
        return 1.0
    import math
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    # p は (0,1) にクリップ
    eps = 1e-6
    if p <= 0:
        p = eps
    elif p >= 1:
        p = 1 - eps
    import math
    return math.log(p / (1.0 - p))


class Command(BaseCommand):
    """
    latest_behavior_side.jsonl から
    「クセ学習用メタモデル（ロジット足し合わせ型）」を学習し、
    JSON に保存するコマンド。

    使い方:
      python manage.py train_behavior_model
      python manage.py train_behavior_model --user 1
    """

    help = "AI 行動データから、クセ学習用メタモデルを学習し JSON 保存する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="対象ユーザーID（省略時は全ユーザーのデータで学習）",
        )

    # --------------------------------------------------
    # メイン処理
    # --------------------------------------------------
    def handle(self, *args, **options) -> None:
        user_id: Optional[int] = options.get("user")

        media_root = Path(settings.MEDIA_ROOT)
        behavior_dir = media_root / "aiapp" / "behavior"
        side_path = behavior_dir / "latest_behavior_side.jsonl"

        if not side_path.exists():
            self.stdout.write(self.style.ERROR(
                f"[train_behavior_model] 学習データがありません: {side_path}"
            ))
            return

        self.stdout.write(
            f"[train_behavior_model] MEDIA_ROOT={media_root} user={user_id!r}"
        )
        self.stdout.write(f"[train_behavior_model] 読み込み元: {side_path}")

        # ---------- JSONL 読み込み ----------
        samples: List[Sample] = []
        total_lines = 0

        try:
            text = side_path.read_text(encoding="utf-8")
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f"[train_behavior_model] 読み込み失敗: {e}"
            ))
            return

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                rec = json.loads(line)
            except Exception:
                continue

            # user フィルタ
            uid = rec.get("user_id")
            if user_id is not None and uid != user_id:
                continue

            label = (rec.get("label") or "").lower()
            # win / lose のみ学習対象（flat / no_position / none は除外）
            if label not in ("win", "lose"):
                continue

            y = 1 if label == "win" else 0

            mode = (rec.get("mode") or "").lower()
            if mode not in ("live", "demo"):
                mode = "other"

            broker = (rec.get("broker") or "").lower() or "unknown"

            sector = str(rec.get("sector") or "(未分類)")

            trend = str(rec.get("trend_daily") or "不明")

            ts_str = str(rec.get("ts") or "")
            time_bucket = _bucket_time_of_day(ts_str)

            atr = _safe_float(rec.get("atr_14"))
            atr_bucket = _bucket_atr(atr)

            slope = _safe_float(rec.get("slope_20"))
            slope_bucket = _bucket_slope(slope)

            samples.append(
                Sample(
                    user_id=uid,
                    mode=mode,
                    broker=broker,
                    sector=sector,
                    trend=trend,
                    time_bucket=time_bucket,
                    atr_bucket=atr_bucket,
                    slope_bucket=slope_bucket,
                    y=y,
                )
            )

        if not samples:
            self.stdout.write(self.style.WARNING(
                "[train_behavior_model] 学習対象となる win/lose データがありません。"
            ))
            return

        self.stdout.write(
            f"[train_behavior_model] 行読み込み: {total_lines} 行 / "
            f"学習サンプル: {len(samples)} 件"
        )

        # ---------- 統計集計（グローバル + 各ファクター） ----------
        # グローバル
        global_wins = 0
        global_trials = 0

        # factor -> value -> (wins, trials)
        factor_stats: Dict[str, Dict[str, Dict[str, float]]] = {
            "broker": defaultdict(lambda: {"wins": 0.0, "trials": 0.0}),
            "mode": defaultdict(lambda: {"wins": 0.0, "trials": 0.0}),
            "sector": defaultdict(lambda: {"wins": 0.0, "trials": 0.0}),
            "trend": defaultdict(lambda: {"wins": 0.0, "trials": 0.0}),
            "time_bucket": defaultdict(lambda: {"wins": 0.0, "trials": 0.0}),
            "atr_bucket": defaultdict(lambda: {"wins": 0.0, "trials": 0.0}),
            "slope_bucket": defaultdict(lambda: {"wins": 0.0, "trials": 0.0}),
        }

        for s in samples:
            global_trials += 1
            if s.y == 1:
                global_wins += 1

            for fname, val in (
                ("broker", s.broker),
                ("mode", s.mode),
                ("sector", s.sector),
                ("trend", s.trend),
                ("time_bucket", s.time_bucket),
                ("atr_bucket", s.atr_bucket),
                ("slope_bucket", s.slope_bucket),
            ):
                st = factor_stats[fname][val]
                st["trials"] += 1.0
                if s.y == 1:
                    st["wins"] += 1.0

        # ---------- ロジット重み計算 ----------
        # ラプラス平滑用
        alpha = 1.0

        # グローバル勝率 → バイアス
        p_global = (global_wins + alpha) / (global_trials + 2 * alpha)
        bias = _logit(p_global)

        self.stdout.write(
            f"[train_behavior_model] 全体勝率: {p_global * 100:.1f}% "
            f"(wins={global_wins} / trials={global_trials})"
        )

        # 各ファクターごとに value ごとの weight (= logit(p_c) - bias) を計算
        factors_out: Dict[str, Any] = {}

        for fname, vdict in factor_stats.items():
            f_info: Dict[str, Any] = {
                "weights": {},
                "stats": {},
            }
            for val, st in vdict.items():
                trials = st["trials"]
                wins = st["wins"]
                if trials <= 0:
                    continue
                p_c = (wins + alpha) / (trials + 2 * alpha)
                weight = _logit(p_c) - bias

                f_info["weights"][val] = weight
                f_info["stats"][val] = {
                    "wins": wins,
                    "trials": trials,
                    "win_rate": p_c,
                }

            factors_out[fname] = f_info

        # ---------- モデル JSON を構築 ----------
        now = timezone.now()
        date_str = now.strftime("%Y%m%d")

        model: Dict[str, Any] = {
            "version": "v1",
            "user_id": user_id,
            "updated_at": now.isoformat(),
            "n_samples": global_trials,
            "global": {
                "wins": global_wins,
                "trials": global_trials,
                "win_rate": p_global,
                "bias": bias,
            },
            "factors": factors_out,
        }

        # ---------- 保存 ----------
        model_dir = behavior_dir / "model"
        model_dir.mkdir(parents=True, exist_ok=True)

        uid_tag = f"u{user_id}" if user_id is not None else "uall"

        dated_path = model_dir / f"{date_str}_behavior_model_{uid_tag}.json"
        latest_path = model_dir / f"latest_behavior_model_{uid_tag}.json"

        try:
            dated_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
            latest_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f"[train_behavior_model] モデル保存に失敗: {e}"
            ))
            return

        # ---------- サマリ表示 ----------
        self.stdout.write("")
        self.stdout.write("===== クセ学習メタモデル サマリ =====")
        self.stdout.write(f"  user_id      : {user_id}")
        self.stdout.write(f"  n_samples    : {global_trials}")
        self.stdout.write(f"  global_win   : {p_global * 100:.1f}%")
        self.stdout.write("")
        self.stdout.write("  broker:")
        for val, st in factor_stats["broker"].items():
            if st["trials"] <= 0:
                continue
            rate = st["wins"] / st["trials"] * 100.0
            self.stdout.write(
                f"    - {val}: trials={int(st['trials'])} wins={int(st['wins'])} "
                f"win_rate={rate:.1f}%"
            )

        self.stdout.write("")
        self.stdout.write(f"  → 保存先: {dated_path}")
        self.stdout.write(f"     latest  : {latest_path}")
        self.stdout.write(self.style.SUCCESS("[train_behavior_model] 完了"))