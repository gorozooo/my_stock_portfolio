# portfolio/management/commands/advisor_train.py
# -*- coding: utf-8 -*-
"""
学習コマンド（精度モニタリングを自動記録）
- 過去の AdviceSession/AdviceItem を簡単な特徴量に変換
- ロジスティック回帰（またはダミー）で係数を推定
- AdvicePolicy を保存（enabled=ON, 旧modelはOFF）
- AdvisorMetrics に学習結果を1行追記（train_acc, n, engine など）

実行:
  python manage.py advisor_train --engine logreg --horizon 7
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Dict, List, Tuple

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from ...models_advisor import AdviceSession, AdviceItem, AdvicePolicy, AdvisorMetrics

# ------- 簡易特徴量（必要最低限） -------
FEATURES = [
    "roi_gap_abs",
    "liquidity_rate_pct",
    "margin_ratio_pct",
    "realized_month_ratio",
    "top_sector_ratio",
    "uncat_sector_ratio",
    "win_ratio",
]

def _f(v):  # safe float
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0

def _build_features(kpi: Dict, sectors: List[Dict]) -> Dict[str, float]:
    total_assets = max(1.0, _f(kpi.get("total_assets")))
    realized_month_ratio = _f(kpi.get("realized_month")) / total_assets

    top_ratio = 0.0
    uncat_ratio = 0.0
    if sectors:
        total_mv = sum(max(0.0, _f(s.get("mv"))) for s in sectors) or 1.0
        top_ratio = _f(sectors[0].get("mv")) / total_mv if sectors else 0.0
        uncat = next((s for s in sectors if s.get("sector") == "未分類"), None)
        if uncat:
            uncat_ratio = _f(uncat.get("mv")) / total_mv

    feats = {
        "roi_gap_abs": _f(kpi.get("roi_gap_abs")),
        "liquidity_rate_pct": _f(kpi.get("liquidity_rate_pct")),
        "margin_ratio_pct": _f(kpi.get("margin_ratio_pct")),
        "realized_month_ratio": realized_month_ratio,
        "top_sector_ratio": top_ratio * 100.0,
        "uncat_sector_ratio": uncat_ratio * 100.0,
        "win_ratio": _f(kpi.get("win_ratio")),
    }
    return {k: float(v) for k, v in feats.items()}


def _collect_dataset(horizon_days: int) -> Tuple[List[List[float]], List[int]]:
    """
    ざっくり学習データ：
      X: セッション時点の特徴量
      y: そのセッション内で「✅ taken が多かったか」を 1/0 で表すダミーラベル
         （本格運用では AdviceItem.outcome の改善度を使うのが理想）
    """
    sessions = list(AdviceSession.objects.order_by("created_at"))
    X: List[List[float]] = []
    y: List[int] = []

    for s in sessions:
        kpi = s.context_json or {}
        sectors = kpi.get("sectors") or []  # 無ければ空
        feats = _build_features(kpi, sectors)
        vec = [feats.get(k, 0.0) for k in FEATURES]

        items = list(s.items.all())
        if not items:
            continue
        taken_ratio = sum(1 for it in items if it.taken) / max(1, len(items))
        X.append(vec)
        y.append(1 if taken_ratio >= 0.5 else 0)

    return X, y


def _train_logreg_like(X: List[List[float]], y: List[int]) -> Dict:
    """
    依存無しの疑似学習（係数=相関係数っぽい重み付け）で十分。
    将来 scikit-learn に差し替えやすいようにインタフェースだけ合わせる。
    """
    n = len(X)
    if n == 0:
        return {"coef": {k: 0.0 for k in FEATURES}, "bias": 0.0, "report": {"train_acc": 0.0, "n": 0}}

    # 各特徴量で単純相関っぽいスコアを作る
    import statistics as st

    ys = y
    mu_y = st.mean(ys)
    coef = {}
    for j, name in enumerate(FEATURES):
        col = [row[j] for row in X]
        mu_x = st.mean(col)
        # 分散が0なら係数0
        var_x = st.pvariance(col) or 1.0
        cov = st.mean([(col[i]-mu_x)*(ys[i]-mu_y) for i in range(n)])
        coef[name] = float(cov / var_x)

    # 疑似予測 → しきい値 0.0
    bias = -mu_y  # 超テキトーな平行移動
    pred = []
    for row in X:
        z = bias + sum(coef[FEATURES[j]] * row[j] for j in range(len(FEATURES)))
        pred.append(1 if z >= 0 else 0)

    acc = sum(1 for i in range(n) if pred[i] == y[i]) / n
    return {"coef": coef, "bias": float(bias), "report": {"train_acc": float(acc), "n": n}}


class Command(BaseCommand):
    help = "AIアドバイザー学習（AdvicePolicy作成＋精度モニタ自動記録）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--engine", type=str, default="logreg", choices=["logreg", "rule"],
                            help="簡易モデルの種類（既定: logreg）")
        parser.add_argument("--horizon", type=int, default=7, help="将来の評価幅（ダミーで保持）")

    def handle(self, *args, **opts):
        engine = opts["engine"]
        horizon = int(opts["horizon"])

        self.stdout.write(f"[advisor_train] engine={engine} horizon={horizon}")

        # 1) データ収集
        X, y = _collect_dataset(horizon_days=horizon)

        # 2) 学習
        if engine == "logreg":
            model = _train_logreg_like(X, y)
        else:  # rule（ダミー）
            model = {"coef": {k: 0.0 for k in FEATURES}, "bias": 0.0, "report": {"train_acc": 0.0, "n": len(X)}}

        coef = model["coef"]
        bias = model["bias"]
        report = model["report"]

        # 3) Policy保存（旧enabledを落として新規ON）
        with transaction.atomic():
            AdvicePolicy.objects.filter(enabled=True).update(enabled=False)
            policy = AdvicePolicy.objects.create(
                kind=AdvicePolicy.Kind.LOGREG if engine == "logreg" else AdvicePolicy.Kind.LINEAR,
                params={
                    "coef": coef,
                    "bias": bias,
                    "features": FEATURES,
                    "norm": {},           # 将来用
                    "horizon_days": horizon,
                    "report": report,
                },
                enabled=True,
            )

            # 4) 精度モニタを記録（これが本題！）
            AdvisorMetrics.objects.create(
                engine=engine,
                policy=policy,
                train_acc=report.get("train_acc", 0.0),
                n=report.get("n", 0),
                notes={"horizon": horizon},
            )

        self.stdout.write(self.style.SUCCESS(
            f"[advisor_train] policy#{policy.id} saved. acc={report.get('train_acc', 0):.3f} n={report.get('n', 0)}"
        ))