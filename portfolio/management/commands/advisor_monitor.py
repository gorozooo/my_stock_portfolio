# -*- coding: utf-8 -*-
"""
AIアドバイザー 精度モニタリング（依存なし）
- 過去の AdviceSession を時系列で追い、horizon 日後のKPIとの差分から “改善度( -1..+1 )” を推定
- 採用済み(✅)の AdviceItem をカテゴリ/メッセージ単位で集計（勝率・平均改善度・信頼度）
- 週次トレンドも算出して monitor.json に保存

実行:
    python manage.py advisor_monitor
オプション:
    --horizon 7              # 何日後のKPIで改善を評価するか（既定: 7）
    --out media/advisor/monitor.json
    --since-days 180         # トレンドに含める期間（既定: 180日）
    --print                  # 結果JSONを標準出力にも表示
"""
from __future__ import annotations
import json
import math
import os
from dataclasses import dataclass
from datetime import timedelta, date
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from ...models_advisor import AdviceSession, AdviceItem


# ------------------------------
# 設定
# ------------------------------
DEFAULT_HORIZON_DAYS = 7
DEFAULT_OUTPUT_PATH = "media/advisor/monitor.json"
DEFAULT_SINCE_DAYS = 180

# メッセージ文面 → カテゴリ簡易マッピング（advisor_learn.py と同等）
CATEGORY_PATTERNS = [
    ("GAP",     ["乖離", "評価ROIと現金ROI"]),      # ROIギャップ整理
    ("LIQ",     ["流動性", "現金化余地"]),          # 流動性アップ
    ("MARGIN",  ["信用比率", "レバレッジ"]),        # 信用圧縮
    ("SECTOR",  ["セクター偏在"]),                  # セクター分散
    ("UNCAT",   ["未分類セクター", "業種タグ"]),     # 未分類タグ整備
    ("REALIZE", ["実現益", "利確", "段階的利確"]),   # 含み益の部分利確
    ("NEGROI",  ["評価ROIが", "損失限定"]),          # 守り提案
]


@dataclass
class Outcome:
    score: float
    details: Dict


# ------------------------------
# ユーティリティ
# ------------------------------
def _get_category(message: str) -> str:
    msg = message or ""
    for cat, keys in CATEGORY_PATTERNS:
        if any(k in msg for k in keys):
            return cat
    return "OTHER"


def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _improve_between(kpi0: Dict, kpi1: Dict) -> Outcome:
    """
    KPIの“良くなった/悪くなった”を1つのスカラーにまとめる簡易ルール。
    - ROI_eval_pct：上がると◎
    - liquidity_rate_pct：上がると◎
    - margin_ratio_pct：下がると◎（なので差は逆符号で評価）
    それぞれをざっくり正規化して平均し、-1.0..+1.0 にクリップ。
    """
    if not kpi0 or not kpi1:
        return Outcome(0.0, {"reason": "missing_kpi"})

    d_roi = _safe_float(kpi1.get("roi_eval_pct")) - _safe_float(kpi0.get("roi_eval_pct"))
    d_liq = _safe_float(kpi1.get("liquidity_rate_pct")) - _safe_float(kpi0.get("liquidity_rate_pct"))
    d_mrg = _safe_float(kpi0.get("margin_ratio_pct")) - _safe_float(kpi1.get("margin_ratio_pct"))  # 低いほど◎

    def clip(x, s):
        if s <= 0:
            return 0.0
        return max(-1.0, min(1.0, x / s))

    roi_norm = clip(d_roi, 50.0)
    liq_norm = clip(d_liq, 40.0)
    mrg_norm = clip(d_mrg, 40.0)

    score = (roi_norm + liq_norm + mrg_norm) / 3.0
    return Outcome(
        score=score,
        details={
            "d_roi": d_roi,
            "d_liq": d_liq,
            "d_margin_ratio": -d_mrg,
            "roi_norm": roi_norm,
            "liq_norm": liq_norm,
            "mrg_norm": mrg_norm,
        },
    )


def _find_future_session(all_sessions: List[AdviceSession], base: AdviceSession, horizon_days: int) -> Optional[AdviceSession]:
    target = base.created_at + timedelta(days=horizon_days)
    later = [s for s in all_sessions if s.created_at >= target]
    return later[0] if later else None


def _iso_week_start(d: date) -> date:
    # 週次トレンド用（月曜始まり）
    return d - timedelta(days=d.weekday())


# ------------------------------
# 本体
# ------------------------------
def build_monitor(horizon_days: int, since_days: int) -> Dict:
    sessions: List[AdviceSession] = list(AdviceSession.objects.order_by("created_at"))
    total_sessions = len(sessions)
    if total_sessions == 0:
        return {
            "generated_at": timezone.now().isoformat(),
            "summary": {"sessions": 0},
            "trend": {},
            "category": {},
            "message": {},
            "adoption": {},
        }

    cutoff_dt = timezone.now() - timedelta(days=since_days)

    # 集計用
    weekly_scores: Dict[str, List[float]] = {}     # 'YYYY-MM-DD(週頭)': [score,...]
    cat_stats: Dict[str, Dict[str, float]] = {}    # cat -> {n,sum,wins}
    msg_stats: Dict[str, Dict[str, float]] = {}    # msg -> {n,sum,wins}
    adoption: Dict[str, Dict[str, float]] = {}     # cat -> {offered, taken}

    def _inc(d: Dict[str, Dict[str, float]], key: str, score: float):
        s = d.setdefault(key, {"n": 0, "sum": 0.0, "wins": 0})
        s["n"] += 1
        s["sum"] += score
        if score > 0:
            s["wins"] += 1

    def _bump_adoption(cat: str, taken: bool):
        a = adoption.setdefault(cat, {"offered": 0, "taken": 0})
        a["offered"] += 1
        if taken:
            a["taken"] += 1

    with transaction.atomic():
        for s in sessions:
            future = _find_future_session(sessions, s, horizon_days)
            if not future:
                continue

            k0 = s.context_json or {}
            k1 = future.context_json or {}
            out = _improve_between(k0, k1)

            # 週次トレンド（最近 since_days のみ）
            if s.created_at >= cutoff_dt:
                wk = _iso_week_start(s.created_at.date()).isoformat()
                weekly_scores.setdefault(wk, []).append(float(out.score))

            # アイテム集計（採用優先・未採用も採用率の分母に計上）
            for it in s.items.all().order_by("-score", "-id"):
                cat = _get_category(it.message or "")
                msg = (it.message or "").strip()

                _bump_adoption(cat, bool(it.taken))
                if it.taken:
                    _inc(cat_stats, cat, float(out.score))
                    _inc(msg_stats, msg, float(out.score))

    # 要約
    def _to_view(d: Dict[str, Dict[str, float]]) -> Dict[str, Dict]:
        out = {}
        for k, s in d.items():
            n = int(s["n"])
            if n <= 0:
                continue
            avg = s["sum"] / max(1, n)
            win = s["wins"] / max(1, n)
            # 信頼度：件数を対数でスケール
            conf = min(1.0, math.log10(1 + n) / math.log10(1 + max(n, 10)))
            out[k] = {
                "count": n,
                "avg_improve": round(avg, 4),
                "win_rate": round(win, 4),
                "confidence": round(conf, 4),
            }
        return out

    # 週次トレンド平均
    trend = {wk: round(sum(vals) / max(1, len(vals)), 4) for wk, vals in sorted(weekly_scores.items())}

    # 採用率
    adoption_view = {}
    for cat, a in adoption.items():
        offered = int(a.get("offered", 0))
        taken = int(a.get("taken", 0))
        rate = (taken / offered) if offered > 0 else 0.0
        adoption_view[cat] = {"offered": offered, "taken": taken, "take_rate": round(rate, 4)}

    # 全体平均改善
    overall = list(trend.values())
    avg_improve = round(sum(overall) / max(1, len(overall)), 4) if overall else 0.0

    return {
        "generated_at": timezone.now().isoformat(),
        "horizon_days": horizon_days,
        "summary": {
            "sessions": total_sessions,
            "avg_improvement": avg_improve,
        },
        "trend": trend,
        "category": _to_view(cat_stats),
        "message": _to_view(msg_stats),
        "adoption": adoption_view,
    }


def save_monitor_json(payload: Dict, out_path: str) -> str:
    if not out_path:
        out_path = DEFAULT_OUTPUT_PATH

    # MEDIA_ROOT を起点に解決（相対パスの場合）
    if not os.path.isabs(out_path):
        base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
        out_path = os.path.join(base, out_path)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path


# ------------------------------
# Django management command
# ------------------------------
class Command(BaseCommand):
    help = "AIアドバイザー: 学習効果の精度モニタリングを実行し、monitor.json を生成します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_DAYS,
                            help=f"何日後のKPIで改善を評価するか（既定: {DEFAULT_HORIZON_DAYS}）")
        parser.add_argument("--out", type=str, default=DEFAULT_OUTPUT_PATH,
                            help=f"保存先パス（既定: {DEFAULT_OUTPUT_PATH}。MEDIA_ROOT 相対可）")
        parser.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS,
                            help=f"トレンドに含める過去日数（既定: {DEFAULT_SINCE_DAYS}）")
        parser.add_argument("--print", action="store_true", help="結果JSONを標準出力にも出力")

    def handle(self, *args, **opts):
        horizon = int(opts["horizon"])
        out_path = str(opts["out"])
        since_days = int(opts["since_days"])
        do_print = bool(opts["print"])

        payload = build_monitor(horizon_days=horizon, since_days=since_days)
        saved = save_monitor_json(payload, out_path)

        if do_print:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))

        self.stdout.write(self.style.SUCCESS(f"[advisor_monitor] monitor saved -> {saved}"))