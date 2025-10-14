# portfolio/management/commands/advisor_train.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import io
import json
import math
import statistics
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from ...models_advisor import AdviceSession, AdviceItem, AdvicePolicy

# === 特徴量設計（既存advisorと整合） ===
FEATURES = [
    "roi_gap_abs",
    "liquidity_rate_pct",
    "margin_ratio_pct",
    "realized_month_ratio",   # realized_month / total_assets
    "top_sector_ratio",       # なくても0でOK（現状セッションにsectors無い想定）
    "uncat_sector_ratio",
    "win_ratio",
]

def _f(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0

def _build_features_from_kpis(kpis: Dict, sectors: Optional[List[Dict]] = None) -> Dict[str, float]:
    total_assets = max(1.0, _f(kpis.get("total_assets")))
    realized_month_ratio = _f(kpis.get("realized_month")) / total_assets

    top_ratio = 0.0
    uncat_ratio = 0.0
    # 現状 AdviceSession.context_json は KPIのみ保存想定なので sector比率は無い→0でOK
    # もし将来 context_json に sectors を含めたらここで計算する
    feats = {
        "roi_gap_abs": _f(kpis.get("roi_gap_abs")),
        "liquidity_rate_pct": _f(kpis.get("liquidity_rate_pct")),
        "margin_ratio_pct": _f(kpis.get("margin_ratio_pct")),
        "realized_month_ratio": realized_month_ratio,
        "top_sector_ratio": top_ratio * 100.0,
        "uncat_sector_ratio": uncat_ratio * 100.0,
        "win_ratio": _f(kpis.get("win_ratio")),
    }
    # NaN等を0へ
    return {k: (0.0 if (v != v) else float(v)) for k, v in feats.items()}

# === シンプルな前処理 ===
@dataclass
class DS:
    X: List[List[float]]
    y: List[int]

def _standardize(X: List[List[float]]) -> Tuple[List[List[float]], Dict[str, Dict[str, float]]]:
    """各次元を (x - mu)/sigma に標準化し、mu/sigma を返す"""
    if not X:
        return X, {}
    cols = list(zip(*X))
    mu = [statistics.fmean(c) if len(c) > 0 else 0.0 for c in cols]
    sigma = []
    for i, c in enumerate(cols):
        try:
            s = statistics.pstdev(c)
            sigma.append(s if s > 1e-8 else 1.0)
        except Exception:
            sigma.append(1.0)
    norm_stats = {FEATURES[i]: {"mu": float(mu[i]), "sigma": float(sigma[i])} for i in range(len(FEATURES))}
    Xz = [[(row[i] - mu[i]) / sigma[i] for i in range(len(FEATURES))] for row in X]
    return Xz, norm_stats

def _pack_rows(rows: List[Dict[str, float]]) -> List[List[float]]:
    return [[float(r.get(k, 0.0)) for k in FEATURES] for r in rows]

# === ターゲット定義 ===
def _label_taken(item: AdviceItem) -> Optional[int]:
    # ユーザーが ✅ したかどうか
    return 1 if item.taken else 0

def _label_outcome(item: AdviceItem) -> Optional[int]:
    # 学習スクリプト（advisor_learn.py）が埋めた outcome.score > 0 を「成功=1」
    if not item.outcome:
        return None
    try:
        return 1 if float(item.outcome.get("score", 0.0)) > 0.0 else 0
    except Exception:
        return None

# === 学習器（scikit-learn/LightGBM） ===
def _train_sklearn(ds: DS, model_kind: str = "logreg"):
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import GradientBoostingClassifier

    if model_kind == "gbdt":
        model = GradientBoostingClassifier(random_state=42)
    else:
        model = LogisticRegression(max_iter=200, n_jobs=None)
    model.fit(ds.X, ds.y)
    return model

def _train_lgbm(ds: DS):
    import lightgbm as lgb
    model = lgb.LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=31,
        random_state=42,
    )
    model.fit(ds.X, ds.y)
    return model

# === メイン学習 ===
def _load_dataset(target: str) -> DS:
    """
    target: 'taken' or 'outcome'
    """
    sessions: List[AdviceSession] = list(AdviceSession.objects.order_by("created_at"))
    Xrows: List[Dict[str, float]] = []
    y: List[int] = []

    for s in sessions:
        kpis = s.context_json or {}
        feats = _build_features_from_kpis(kpis)
        for it in s.items.all():
            if target == "taken":
                lab = _label_taken(it)
            else:
                lab = _label_outcome(it)
                if lab is None:
                    continue
            Xrows.append(feats)
            y.append(int(lab))

    X = _pack_rows(Xrows)
    return DS(X=X, y=y)

def _blob_dump(model) -> bytes:
    import joblib
    bio = io.BytesIO()
    joblib.dump(model, bio)
    return bio.getvalue()

def _short_report(model, ds: DS) -> Dict[str, float]:
    # 超簡易に学習データでの精度目安（厳密な汎化性能はCV/validでやる）
    try:
        acc = float(model.score(ds.X, ds.y))
    except Exception:
        acc = 0.0
    return {"train_acc": round(acc, 4), "n": len(ds.y)}

def _upsert_policy(kind: AdvicePolicy.Kind, target: str, model_blob: bytes, norm: Dict, extras: Dict):
    """
    AdvicePolicy を新規作成し、enabled=True にして他を無効化
    """
    with transaction.atomic():
        AdvicePolicy.objects.update(enabled=False)
        p = AdvicePolicy.objects.create(
            kind=kind,
            params={
                "features": FEATURES,
                "target": target,
                "norm": norm,
                **extras,
            },
            model_blob=model_blob,
            enabled=True,
            updated_at=timezone.now(),
        )
    return p

# === Django management command ===
class Command(BaseCommand):
    help = "AIアドバイザー: セッションログからMLモデルを学習し、AdvicePolicy (SKLEARN/LGBM) に保存します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--target", choices=["taken", "outcome"], default="outcome",
                            help="学習ターゲット: taken=採用予測 / outcome=改善成功予測（既定）")
        parser.add_argument("--engine", choices=["logreg", "gbdt", "lgbm"], default="logreg",
                            help="学習エンジン（logreg/gbdt=sklearn, lgbm=LightGBM）")
        parser.add_argument("--min-samples", type=int, default=50,
                            help="学習に必要な最小サンプル数（既定:50）")
        parser.add_argument("--dry-run", action="store_true", help="保存せず評価だけ実施")
        parser.add_argument("--print", action="store_true", help="学習結果・サマリを標準出力")

    def handle(self, *args, **opts):
        target = str(opts["target"])
        engine = str(opts["engine"])
        min_samples = int(opts["min_samples"])
        dry = bool(opts["dry_run"])
        do_print = bool(opts["print"])

        ds = _load_dataset(target=target)
        if len(ds.y) < min_samples:
            self.stdout.write(self.style.WARNING(
                f"[advisor_train] サンプル不足: {len(ds.y)} < {min_samples}（学習スキップ）"
            ))
            return

        # 標準化
        Xz, norm = _standardize(ds.X)
        ds = DS(X=Xz, y=ds.y)

        # 学習
        if engine == "lgbm":
            model = _train_lgbm(ds)
            kind = AdvicePolicy.Kind.SKLEARN  # 既存推論ルーチンで扱うためSKLEARN扱いに統一
        else:
            model = _train_sklearn(ds, model_kind=("gbdt" if engine == "gbdt" else "logreg"))
            kind = AdvicePolicy.Kind.SKLEARN

        rpt = _short_report(model, ds)
        if do_print:
            self.stdout.write(json.dumps({
                "target": target, "engine": engine, "report": rpt, "features": FEATURES
            }, ensure_ascii=False, indent=2))

        if dry:
            self.stdout.write(self.style.WARNING("[advisor_train] dry-run: 保存しません"))
            return

        # 保存（既存の AdvicePolicy を全部無効化→新規作成 enabled=True）
        blob = _blob_dump(model)
        extras = {
            "engine": engine,
            "report": rpt,
        }
        p = _upsert_policy(kind=kind, target=target, model_blob=blob, norm=norm, extras=extras)
        self.stdout.write(self.style.SUCCESS(
            f"[advisor_train] Policy saved id={p.id} kind={p.kind} target={target} engine={engine} report={rpt}"
        ))