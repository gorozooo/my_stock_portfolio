# -*- coding: utf-8 -*-
from __future__ import annotations
import json, os, shutil
from pathlib import Path
from datetime import timedelta, datetime
from typing import Dict, List, Optional

from django.core.management.base import BaseCommand, CommandParser
from django.core.management import call_command
from django.conf import settings
from django.utils import timezone

from ...models_advisor import AdviceSession

# ===== 改善スコア（自己評価） =====
def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _improve_between(k0: Dict, k1: Dict) -> float:
    """ROI↑, 流動性↑, 信用比率↓ を + 評価。ざっくり正規化の平均。"""
    if not k0 or not k1:
        return 0.0
    d_roi = _safe_float(k1.get("roi_eval_pct")) - _safe_float(k0.get("roi_eval_pct"))
    d_liq = _safe_float(k1.get("liquidity_rate_pct")) - _safe_float(k0.get("liquidity_rate_pct"))
    d_mrg = _safe_float(k0.get("margin_ratio_pct")) - _safe_float(k1.get("margin_ratio_pct"))  # 低いほど◎
    def clip(x, s): return max(-1.0, min(1.0, x / s)) if s else 0.0
    return (clip(d_roi, 50.0) + clip(d_liq, 40.0) + clip(d_mrg, 40.0)) / 3.0

def _calc_self_score(horizon_days: int = 7, since_days: int = 60) -> Optional[float]:
    """直近 since_days の AdviceSession を horizon_days 間隔で比較し平均改善スコア。"""
    cutoff = timezone.now() - timedelta(days=since_days)
    sessions: List[AdviceSession] = list(AdviceSession.objects.filter(created_at__gte=cutoff).order_by("created_at"))
    if len(sessions) < 2:
        return None

    def find_future(idx: int):
        base = sessions[idx]
        target = base.created_at + timedelta(days=horizon_days)
        for j in range(idx + 1, len(sessions)):
            if sessions[j].created_at >= target:
                return sessions[j]
        return None

    ssum = 0.0
    n = 0
    for i, s0 in enumerate(sessions):
        s1 = find_future(i)
        if not s1:
            continue
        ssum += _improve_between(s0.context_json or {}, s1.context_json or {})
        n += 1
    return (ssum / n) if n > 0 else None

# ===== コマンド本体 =====
class Command(BaseCommand):
    help = "advisor_learn を実行し policy.json を更新、historyへ日付付きで保存し、自己評価(self_score)を付与する"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=90, help="学習に使う過去日数（既定:90）")
        parser.add_argument("--horizon", type=int, default=7, help="自己評価の比較間隔（日）既定:7")
        parser.add_argument("--out", type=str, default="media/advisor/policy.json", help="出力先（既定: media/advisor/policy.json）")
        parser.add_argument("--bias", type=float, default=1.0, help="全体バイアス（既定:1.0）")
        parser.add_argument("--clip-low", type=float, default=0.80, dest="clip_low", help="重み下限（既定:0.80）")
        parser.add_argument("--clip-high", type=float, default=1.30, dest="clip_high", help="重み上限（既定:1.30）")
        parser.add_argument("--since", type=int, default=60, help="自己評価に使う過去日数（既定:60）")
        parser.add_argument("--print", action="store_true", help="最終policyを標準出力へも表示")

    def handle(self, *args, **opts):
        days = int(opts["days"])
        horizon = int(opts["horizon"])
        out_rel = str(opts["out"])
        bias = float(opts["bias"])
        clip_low = float(opts["clip_low"])
        clip_high = float(opts["clip_high"])
        since = int(opts["since"])
        do_print = bool(opts["print"])

        # 1) 既存 advisor_learn を呼び出して policy.json を再生成
        call_command(
            "advisor_learn",
            "--days", str(days),
            "--out", out_rel,
            "--bias", str(bias),
            "--clip_low", str(clip_low),
            "--clip_high", str(clip_high),
            verbosity=0
        )

        # 2) 読み込み → 自己評価を付与
        media_root = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
        out_path = Path(media_root) / out_rel if not os.path.isabs(out_rel) else Path(out_rel)
        if not out_path.exists():
            raise RuntimeError(f"policy not found: {out_path}")

        with open(out_path, "r", encoding="utf-8") as f:
            policy = json.load(f)

        self_score = _calc_self_score(horizon_days=horizon, since_days=since)
        policy["self_score"] = round(float(self_score), 6) if self_score is not None else None
        policy["generated_at"] = timezone.now().isoformat()

        # 3) 上書き保存
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(policy, f, ensure_ascii=False, indent=2)

        # 4) 履歴へコピー（policy_YYYY-MM-DD.json）
        hist_dir = out_path.parent / "history"
        hist_dir.mkdir(parents=True, exist_ok=True)
        stamp = timezone.now().strftime("%Y-%m-%d")
        hist_path = hist_dir / f"policy_{stamp}.json"
        with open(hist_path, "w", encoding="utf-8") as f:
            json.dump(policy, f, ensure_ascii=False, indent=2)

        if do_print:
            self.stdout.write(json.dumps(policy, ensure_ascii=False, indent=2))

        self.stdout.write(self.style.SUCCESS(
            f"[advisor_auto_learn] updated → {out_path} ; archived → {hist_path} ; self_score={policy['self_score']}"
        ))