# portfolio/management/commands/advisor_autotune.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import json
from pathlib import Path
from datetime import timedelta
from typing import Dict, Any, Tuple, Optional

from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from ...models_advisor import AdviceItem


# ===================== ユーティリティ =====================
def _media_root() -> str:
    base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    return base

def _policy_path() -> Path:
    rel = getattr(settings, "ADVISOR_POLICY_PATH", "media/advisor/policy.json")
    p = Path(rel)
    if not p.is_absolute():
        p = Path(_media_root()) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _load_policy(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _dump_policy(path: Path, obj: Dict[str, Any]) -> None:
    txt = json.dumps(obj, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(txt)
    os.replace(tmp, path)

def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _is_rs_related(msg: str) -> bool:
    """
    RS関連の助言をざっくり判定。
    - セクター相対強弱の語彙 / PF相対強弱 / セクター偏在 など
    """
    if not msg:
        return False
    keys = [
        "相対強弱", "PF相対強弱", "セクター", "強気", "弱気", "比率圧縮",
        "段階利確", "ヘッジ", "圧縮", "偏在"
    ]
    m = str(msg)
    return any(k in m for k in keys)

def _default_thresholds() -> Tuple[float, float]:
    # 既定（環境適応前のベース）
    return (-0.25, 0.35)


# ===================== しきい値オートチューン =====================
def _autotune_rs_thresholds(
    since_days: int = 90,
    target_take_rate: float = 0.55,
    step: float = 0.05,
    weak_bounds: Tuple[float, float] = (-0.50, -0.05),
    strong_bounds: Tuple[float, float] = (0.15, 0.60),
) -> Tuple[float, float, Dict[str, Any]]:
    """
    過去N日の AdviceItem から RS関連メッセージの採用率を計算し、
    policy.json の rs_thresholds を微調整するための新値を返す。

    ロジック（安全側の小さな自動調整）:
      - rs関連アイテムのみ対象（_is_rs_related）
      - 採用率 > target_take_rate + 0.05 → 発火し過ぎ → 強め方向へ
          weak を 低め（よりマイナス方向）へ  step だけ移動（=弱気を検出しにくく）
          strong を 高め方向へ               step だけ移動（=強気を検出しにくく）
      - 採用率 < target_take_rate - 0.05 → 発火不足 → 緩め方向へ
          weak を 高め（ゼロ方向）へ         step だけ移動（=弱気を検出しやすく）
          strong を 低め方向へ               step だけ移動（=強気を検出しやすく）
      - その間なら据え置き
    """
    since = timezone.now() - timedelta(days=since_days)

    qs = AdviceItem.objects.filter(created_at__gte=since).only("message", "taken")
    n_all = 0
    n_taken = 0
    for it in qs:
        msg = getattr(it, "message", "") or ""
        if not _is_rs_related(msg):
            continue
        n_all += 1
        if bool(getattr(it, "taken", False)):
            n_taken += 1

    # 現状統計
    take_rate = (n_taken / n_all) if n_all > 0 else 0.0

    # policy から現行しきい値を読めるならそれをベース、無ければデフォルト
    pol = _load_policy(_policy_path())
    rs = pol.get("rs_thresholds") if isinstance(pol.get("rs_thresholds"), dict) else {}
    weak = float(rs.get("weak", _default_thresholds()[0]))
    strong = float(rs.get("strong", _default_thresholds()[1]))

    margin = 0.05  # デッドバンド（±5%）
    moved = False
    direction = "hold"

    if take_rate > (target_take_rate + margin):
        # 発火し過ぎ → 厳しく（検出しにくく）
        weak = _clip(weak - step, *weak_bounds)     # より負側へ
        strong = _clip(strong + step, *strong_bounds)
        moved = True
        direction = "tighten"
    elif take_rate < (target_take_rate - margin):
        # 発火不足 → 緩く（検出しやすく）
        weak = _clip(weak + step, *weak_bounds)     # 0に近づける
        strong = _clip(strong - step, *strong_bounds)
        moved = True
        direction = "loosen"

    # weak < strong の最低条件を保証（崩れたら元に戻す）
    if not (weak_bounds[0] <= weak < strong <= strong_bounds[1]):
        weak, strong = _default_thresholds()
        direction = "reset"

    meta = dict(
        window_days=since_days,
        n_items=n_all,
        n_taken=n_taken,
        take_rate=round(take_rate, 4),
        target_take_rate=target_take_rate,
        step=step,
        direction=direction,
    )
    return weak, strong, meta


# ===================== コマンド本体 =====================
class Command(BaseCommand):
    help = "過去の採用率から RS の弱気/強気しきい値(rs_thresholds)を自動微調整して policy.json を更新"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90, help="学習に使う期間（日数） default=90")
        parser.add_argument("--target", type=float, default=0.55, help="目標採用率 default=0.55")
        parser.add_argument("--step", type=float, default=0.05, help="1回の調整幅 default=0.05")
        parser.add_argument("--dry-run", action="store_true", help="書き込みせず結果だけ表示")

    def handle(self, *args, **opts):
        days = int(opts["days"])
        target = float(opts["target"])
        step = float(opts["step"])
        dry = bool(opts.get("dry_run"))

        policy_file = _policy_path()
        policy = _load_policy(policy_file)

        # 既存の thresholds
        prev_w = float((policy.get("rs_thresholds") or {}).get("weak", _default_thresholds()[0]))
        prev_s = float((policy.get("rs_thresholds") or {}).get("strong", _default_thresholds()[1]))

        # 新値を算出
        new_w, new_s, meta = _autotune_rs_thresholds(
            since_days=days,
            target_take_rate=target,
            step=step,
        )

        # policy へ反映
        policy.setdefault("version", policy.get("version", 2))
        policy.setdefault("updated_at", timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"))

        policy["rs_thresholds"] = {
            "weak": round(new_w, 3),
            "strong": round(new_s, 3),
        }
        # ログ用に前回値とメタ統計も保存
        policy.setdefault("signals", {})
        policy["signals"].setdefault("autotune", {})
        policy["signals"]["autotune"] = {
            **meta,
            "prev": {"weak": prev_w, "strong": prev_s},
            "now": {"weak": policy["rs_thresholds"]["weak"], "strong": policy["rs_thresholds"]["strong"]},
            "updated_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        if dry:
            self.stdout.write(self.style.NOTICE(
                f"[DRY-RUN] rs_thresholds: {prev_w:+.3f}/{prev_s:+.3f} -> {new_w:+.3f}/{new_s:+.3f} | meta={meta}"
            ))
            return

        _dump_policy(policy_file, policy)
        self.stdout.write(self.style.SUCCESS(
            f"Updated policy.json rs_thresholds: {prev_w:+.3f}/{prev_s:+.3f} -> {new_w:+.3f}/{new_s:+.3f}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"meta={meta}  file={policy_file}"
        ))