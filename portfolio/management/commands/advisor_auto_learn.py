# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from datetime import timedelta
import json, tempfile, os
from typing import Dict, List, Optional

from django.core.management.base import BaseCommand
from django.utils import timezone

from portfolio.models_advisor import AdviceSession, AdviceItem

# ---------------------------
# ユーティリティ
# ---------------------------
def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _atomic_write(fp: Path, data: str) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(fp.parent)) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    os.replace(tmp_path, fp)

def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

# ---------------------------
# KPI 改善スコア（＋実損・配当ボーナス）
# ---------------------------
def _improve_score(k0: Dict, k1: Dict) -> Dict[str, float]:
    """
    返り値:
      {
        score:      -1..+1（総合）
        base:       -1..+1（ROI/流動性/信用）
        realized:   -1..+1（実現益ボーナス）
        dividend:   -1..+1（配当ボーナス）
      }
    """
    if not k0 or not k1:
        return dict(score=0.0, base=0.0, realized=0.0, dividend=0.0)

    # --- 基本3要素（従来） ---
    d_roi = _safe_float(k1.get("roi_eval_pct")) - _safe_float(k0.get("roi_eval_pct"))
    d_liq = _safe_float(k1.get("liquidity_rate_pct")) - _safe_float(k0.get("liquidity_rate_pct"))
    d_mrg = _safe_float(k0.get("margin_ratio_pct")) - _safe_float(k1.get("margin_ratio_pct"))  # 低いほど◎

    roi_norm = _clip(d_roi / 50.0, -1.0, 1.0)   # ±50pt → ±1
    liq_norm = _clip(d_liq / 40.0, -1.0, 1.0)   # ±40pt → ±1
    mrg_norm = _clip(d_mrg / 40.0, -1.0, 1.0)   # ±40pt → ±1
    base = (roi_norm + liq_norm + mrg_norm) / 3.0

    # --- 実損・配当の“実額ベース”ボーナス ---
    # realized_cum, dividend_cum が KPI に入っている前提（home で格納済）
    invested = max(1.0, _safe_float(k0.get("invested")))

    realized_cum0 = _safe_float(k0.get("realized_cum"))
    realized_cum1 = _safe_float(k1.get("realized_cum"))
    d_realized = (realized_cum1 - realized_cum0) / invested  # 投下資金比 (%ではなく比率)
    # 5% 利益増を +1.0 相当でクリップ
    realized_norm = _clip(d_realized / 0.05, -1.0, 1.0)

    dividend_cum0 = _safe_float(k0.get("dividend_cum"))
    dividend_cum1 = _safe_float(k1.get("dividend_cum"))
    d_dividend = (dividend_cum1 - dividend_cum0) / invested
    # 1% 配当増を +1.0 相当でクリップ（小さめに効かせる）
    dividend_norm = _clip(d_dividend / 0.01, -1.0, 1.0)

    # 重み（好みに合わせてチューニング可）
    w_base = 0.7
    w_real = 0.2
    w_div  = 0.1

    score = _clip(w_base * base + w_real * realized_norm + w_div * dividend_norm, -1.0, 1.0)
    return dict(score=score, base=base, realized=realized_norm, dividend=dividend_norm)

# ---------------------------
# 学習ロジック
# ---------------------------
def _learn(days: int, bias: float, clip_low: float, clip_high: float, horizon_days: int = 7) -> Dict:
    """
    ・過去N日の AdviceSession を時系列に走査
    ・horizon 日後のセッションを見つけ KPI 差分から「改善スコア」を算出
    ・“採用された提案(kind)” にスコアを付与 → kind_weight を推定
    """
    since = timezone.now() - timedelta(days=days)
    sessions: List[AdviceSession] = list(
        AdviceSession.objects.filter(created_at__gte=since).order_by("created_at")
    )

    if len(sessions) < 2:
        # データが乏しい場合のデフォルト
        return dict(
            version=2,
            updated_at=timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            bias=bias,
            info="no_sessions_or_insufficient_history",
            kind_weight={
                "REBALANCE": 1.0, "ADD_CASH": 1.0, "TRIM_WINNERS": 1.0,
                "CUT_LOSERS": 1.0, "REDUCE_MARGIN": 1.0
            },
            metrics=dict(samples=0)
        )

    # horizon 後セッション検索（最短マッチ）
    def find_future(idx: int) -> Optional[AdviceSession]:
        base = sessions[idx]
        target = base.created_at + timedelta(days=horizon_days)
        for j in range(idx + 1, len(sessions)):
            if sessions[j].created_at >= target:
                return sessions[j]
        return None

    # 集計
    per_kind = {}  # kind -> {"n":採用数, "sum":スコア合計}
    samples = 0

    for i, s0 in enumerate(sessions):
        s1 = find_future(i)
        if not s1:
            continue
        k0 = s0.context_json or {}
        k1 = s1.context_json or {}
        sc = _improve_score(k0, k1)
        total_score = float(sc["score"])

        # “採用された提案” を重視してカウント
        for it in s0.items.all():
            if not it.taken:
                continue
            kind = (it.kind or "REBALANCE").upper()
            acc = per_kind.setdefault(kind, {"n": 0, "sum": 0.0})
            acc["n"] += 1
            acc["sum"] += total_score
            samples += 1

    if not per_kind:
        # 採用ログがない場合の救済
        return dict(
            version=2,
            updated_at=timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            bias=bias,
            info="no_taken_items",
            kind_weight={"REBALANCE": 1.0, "ADD_CASH": 1.0, "TRIM_WINNERS": 1.0,
                         "CUT_LOSERS": 1.0, "REDUCE_MARGIN": 1.0},
            metrics=dict(samples=0)
        )

    # kindごとの平均スコア → 正規化（平均=1.0） → クリップ → バイアス乗算
    # 平均スコア（負〜正）を [0.8, 1.3] 付近の重みに写像する簡易ルール
    avg_by_kind = {k: (v["sum"] / max(1, v["n"])) for k, v in per_kind.items()}
    # 全体平均
    gavg = sum(avg_by_kind.values()) / max(1, len(avg_by_kind))
    # gavg=0 の場合のスケーリング対策
    if abs(gavg) < 1e-9:
        gavg = 1e-9

    # 比率的に “他より良い/悪い” を重みに反映
    raw_weight = {k: (v / gavg) for k, v in avg_by_kind.items()}

    # クリップ＋バイアス
    kind_weight = {
        k: _clip(bias * w, clip_low, clip_high)
        for k, w in raw_weight.items()
    }

    return dict(
        version=2,
        updated_at=timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        bias=bias,
        horizon_days=horizon_days,
        kind_weight=kind_weight,
        metrics=dict(
            samples=samples,
            kinds=len(kind_weight),
            window_days=days,
        ),
        notes="score = 0.7*{ROI/LIQ/MARGIN} + 0.2*{realized_gain} + 0.1*{dividend_gain}"
    )

# ---------------------------
# メインコマンド
# ---------------------------
class Command(BaseCommand):
    help = "過去の提案採用とKPIの変化に、実現損益・配当も考慮して policy.json を自動生成（履歴も保存）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90, help="学習に使う日数（過去N日） default=90")
        parser.add_argument("--out", type=str, default="media/advisor/policy.json",
                            help="出力先パス（相対/絶対可）")
        parser.add_argument("--bias", type=float, default=1.0, help="全体バイアス default=1.0")
        parser.add_argument("--clip_low", type=float, default=0.80, help="重みの下限 default=0.80")
        parser.add_argument("--clip_high", type=float, default=1.30, help="重みの上限 default=1.30")
        parser.add_argument("--horizon", type=int, default=7, help="何日後のKPIで改善を評価するか default=7")
        parser.add_argument("--no-history", action="store_true",
                            help="true の場合、履歴ファイル(policy_YYYY-MM-DD.json)を残さない")

    def handle(self, *args, **opts):
        days = int(opts["days"])
        out_path = Path(opts["out"])
        bias = float(opts["bias"])
        clip_low = float(opts["clip_low"])
        clip_high = float(opts["clip_high"])
        horizon = int(opts["horizon"])
        keep_history = not bool(opts["no_history"])

        policy = _learn(days=days, bias=bias, clip_low=clip_low, clip_high=clip_high, horizon_days=horizon)

        # 保存（atomic）
        _atomic_write(out_path, json.dumps(policy, ensure_ascii=False, indent=2))
        self.stdout.write(self.style.SUCCESS(f"[advisor_auto_learn] wrote → {out_path}"))

        # 履歴も保存
        if keep_history:
            stamp = timezone.now().strftime("%Y-%m-%d")
            hist_path = out_path.parent / "history" / f"policy_{stamp}.json"
            _atomic_write(hist_path, json.dumps(policy, ensure_ascii=False, indent=2))
            self.stdout.write(self.style.SUCCESS(f"[advisor_auto_learn] history → {hist_path}"))