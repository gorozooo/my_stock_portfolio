from __future__ import annotations
from django.core.management.base import BaseCommand
from django.db import transaction

from ...models_advisor import AdvisorProposal, AdvicePolicy
from statistics import mean, pstdev

# sklearn があれば使う（無ければ係数法で代替）
try:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    import joblib, io
    HAS_SK = True
except Exception:
    HAS_SK = False


FEATURES = [
    "roi_gap_abs",
    "liquidity_rate_pct",
    "margin_ratio_pct",
    "realized_month_ratio",
    "top_sector_ratio",
    "uncat_sector_ratio",
    "win_ratio",
]


def _rows_from_db():
    rows = []
    qs = AdvisorProposal.objects.select_related("item")
    for p in qs:
        x = p.features or {}
        # 欠損穴埋め
        row = [float(x.get(k, 0.0) or 0.0) for k in FEATURES]
        y = 1.0 if bool(p.label_taken) else 0.0
        rows.append((row, y))
    return rows


def _standardize(X):
    # 特徴量ごとに μ, σ を計算して標準化
    cols = list(zip(*X)) if X else [[] for _ in FEATURES]
    mu = [mean(c) if c else 0.0 for c in cols]
    sigma = [pstdev(c) if (c and pstdev(c) > 0) else 1.0 for c in cols]
    Xs = []
    for r in X:
        Xs.append([(r[i] - mu[i]) / sigma[i] for i in range(len(FEATURES))])
    norm = {FEATURES[i]: {"mu": mu[i], "sigma": sigma[i]} for i in range(len(FEATURES))}
    return Xs, norm


class Command(BaseCommand):
    help = "AdvisorProposal を学習して AdvicePolicy を更新（ロジ回帰 or 簡易線形）"

    def add_arguments(self, parser):
        parser.add_argument("--name", default="default")
        parser.add_argument("--version", default="v1")
        parser.add_argument("--enable", action="store_true", help="学習後にこのポリシーを有効化する")

    def handle(self, *args, **opts):
        rows = _rows_from_db()
        if not rows:
            self.stdout.write(self.style.WARNING("No training data (AdvisorProposal)."))
            return

        X = [r[0] for r in rows]
        y = [r[1] for r in rows]
        Xs, norm = _standardize(X)

        params = {"norm": norm}
        model_blob = None
        kind = AdvicePolicy.Kind.LINEAR

        if HAS_SK and len(set(y)) > 1:
            # ロジスティック回帰
            clf = LogisticRegression(max_iter=200)
            clf.fit(np.array(Xs), np.array(y))
            coef = {FEATURES[i]: float(clf.coef_[0][i]) for i in range(len(FEATURES))}
            bias = float(clf.intercept_[0])
            params.update({"coef": coef, "bias": bias})
            kind = AdvicePolicy.Kind.LOGREG

            # モデルも保存（任意）
            buf = io.BytesIO()
            joblib.dump(clf, buf)
            model_blob = buf.getvalue()
        else:
            # 係数法（相関っぽい重みを簡易に計算）——最小限の代替
            # 特徴量とyの共分散符号を重みとする
            weights = {}
            for j, f in enumerate(FEATURES):
                col = [r[j] for r in Xs]
                mu_x = mean(col) if col else 0.0
                mu_y = mean(y) if y else 0.0
                cov = sum((col[i] - mu_x) * (y[i] - mu_y) for i in range(len(y))) / max(1, len(y))
                weights[f] = float(cov)
            # 正規化
            s = sum(abs(v) for v in weights.values()) or 1.0
            coef = {k: v / s for k, v in weights.items()}
            bias = 0.0
            params.update({"coef": coef, "bias": bias})
            kind = AdvicePolicy.Kind.LINEAR

        with transaction.atomic():
            pol, _ = AdvicePolicy.objects.update_or_create(
                name=opts["name"], version=opts["version"],
                defaults={
                    "kind": kind,
                    "params": params,
                    "model_blob": model_blob,
                    "enabled": bool(opts.get("enable")),
                }
            )

            # 既存で別の enabled があれば OFF（単一有効にしたい場合）
            if opts.get("enable"):
                AdvicePolicy.objects.exclude(id=pol.id).filter(enabled=True).update(enabled=False)

        self.stdout.write(self.style.SUCCESS(
            f"Trained policy {pol.name}/{pol.version} kind={pol.kind} enabled={pol.enabled}"
        ))