# -*- coding: utf-8 -*-
"""
train_lgbm_models.py

目的:
  build_ml_dataset で生成した学習データ（latest_train.parquet/csv）から
  LightGBM のモデル群を学習し、スナップショットとして保存する。

出力:
  media/aiapp/ml/models/YYYYMMDD_HHMMSS/
    - meta.json
    - model_pwin.txt
    - model_ev.txt
    - model_hold_days.txt   (任意)
    - model_tp_first.txt    (任意)
    - feature_cols.json
    - label_maps.json       (tp_first用)

追加（今回）:
  meta.json に評価指標 metrics を保存
    - p_win: auc, logloss
    - ev: rmse, mae
    - hold_days_pred: mae
    - tp_first: accuracy, logloss
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

JST = dt_timezone(timedelta(hours=9))

# LightGBM は外部依存（本番で使うので try せず必須扱い）
import lightgbm as lgb


def _now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


def _read_latest_train() -> pd.DataFrame:
    base = Path(settings.MEDIA_ROOT) / "aiapp" / "ml" / "train"
    p_parq = base / "latest_train.parquet"
    p_csv = base / "latest_train.csv"

    if p_parq.exists():
        return pd.read_parquet(p_parq)
    if p_csv.exists():
        return pd.read_csv(p_csv)
    raise FileNotFoundError(f"latest_train not found: {p_parq} or {p_csv}")


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()

    # 期待: 必須列
    required = ["y_label", "y_pl"]
    for c in required:
        if c not in d.columns:
            raise ValueError(f"missing required column: {c}")

    # label 正規化
    d["y_label"] = d["y_label"].astype(str).str.lower().str.strip()
    d = d[d["y_label"].isin(["win", "lose", "flat"])].copy()

    # 基本は数値化
    for c in d.columns:
        if c.endswith("_id"):
            d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).astype(int)

    # 数値列の NaN は残す（LightGBMが扱える）
    return d


def _split_train_valid(df: pd.DataFrame, valid_ratio: float = 0.2, seed: int = 42) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if len(df) < 50:
        # 少ない時は全部trainに寄せる（壊さない）
        return df, df.iloc[:0].copy()

    rng = np.random.default_rng(seed)
    idx = np.arange(len(df))
    rng.shuffle(idx)

    n_valid = int(round(len(df) * valid_ratio))
    valid_idx = idx[:n_valid]
    train_idx = idx[n_valid:]

    return df.iloc[train_idx].copy(), df.iloc[valid_idx].copy()


def _feature_cols(df: pd.DataFrame) -> List[str]:
    """
    未来リーク防止:
      - Xは当日確定の特徴量 + 設計値 + 文脈ID
      - y_* / eval_* / stars などは絶対に入れない
    """
    deny_prefix = ("y_", "eval_", "stars")
    deny_cols = set()
    for c in df.columns:
        if c.startswith(deny_prefix):
            deny_cols.add(c)

    # 使う候補（このプロジェクトの設計に合わせる）
    candidates = [
        # feature snapshot core
        "ATR14", "SLOPE_25", "RET_20", "RSI14", "BB_Z", "VWAP_GAP_PCT",
        # design
        "design_rr", "design_risk", "design_reward", "risk_atr", "reward_atr",
        # context
        "score_100",
        "side_id", "style_id", "horizon_id", "sector_id", "universe_id", "mode_id",
    ]
    cols = [c for c in candidates if c in df.columns and c not in deny_cols]
    if not cols:
        raise ValueError("no feature columns found. dataset columns mismatch.")
    return cols


def _bin_y_win(y_label: pd.Series) -> np.ndarray:
    return (y_label.astype(str).str.lower().str.strip() == "win").astype(int).to_numpy()


def _ev_target(df: pd.DataFrame) -> np.ndarray:
    """
    EVの教師:
      優先: y_r（R） → 無ければ y_pl
    """
    if "y_r" in df.columns and df["y_r"].notna().sum() >= max(20, int(len(df) * 0.1)):
        y = pd.to_numeric(df["y_r"], errors="coerce")
        y = y.fillna(0.0)
        return y.to_numpy(dtype=float)
    y = pd.to_numeric(df["y_pl"], errors="coerce").fillna(0.0)
    return y.to_numpy(dtype=float)


def _tp_first_map() -> Dict[str, int]:
    # 3クラス固定（再現性）
    return {"none": 0, "tp_first": 1, "sl_first": 2}


# =========================
# metrics (numpy only)
# =========================

def _sigmoid_clip(p: np.ndarray) -> np.ndarray:
    p2 = np.asarray(p, dtype=float)
    # LightGBMは0..1を返すが念のため
    p2 = np.clip(p2, 1e-12, 1 - 1e-12)
    return p2


def _binary_logloss(y_true: np.ndarray, p_pred: np.ndarray) -> float:
    y = np.asarray(y_true, dtype=float)
    p = _sigmoid_clip(p_pred)
    loss = -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))
    return float(np.mean(loss)) if len(loss) else 0.0


def _auc_roc(y_true: np.ndarray, p_pred: np.ndarray) -> Optional[float]:
    """
    軽量AUC（順位和ベース）。
    validに win/lose が偏りすぎると None。
    """
    y = np.asarray(y_true, dtype=int)
    p = np.asarray(p_pred, dtype=float)
    n_pos = int(np.sum(y == 1))
    n_neg = int(np.sum(y == 0))
    if n_pos == 0 or n_neg == 0:
        return None

    # ranks with ties: average ranks
    order = np.argsort(p)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(p) + 1, dtype=float)

    # tie handling
    # 同値の範囲は平均順位にする
    sorted_p = p[order]
    i = 0
    while i < len(sorted_p):
        j = i
        while j + 1 < len(sorted_p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        if j > i:
            avg_rank = (i + 1 + j + 1) / 2.0
            ranks[order[i:j + 1]] = avg_rank
        i = j + 1

    sum_ranks_pos = float(np.sum(ranks[y == 1]))
    auc = (sum_ranks_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return float(auc)


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    if len(y) == 0:
        return 0.0
    return float(np.sqrt(np.mean((p - y) ** 2)))


def _mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    if len(y) == 0:
        return 0.0
    return float(np.mean(np.abs(p - y)))


def _multiclass_logloss(y_true: np.ndarray, proba: np.ndarray, num_class: int = 3) -> float:
    """
    y_true: (n,)
    proba: (n, num_class)
    """
    y = np.asarray(y_true, dtype=int)
    P = np.asarray(proba, dtype=float)
    if len(y) == 0:
        return 0.0
    if P.ndim != 2 or P.shape[1] != num_class:
        return 0.0

    P = np.clip(P, 1e-12, 1 - 1e-12)
    # normalize
    P = P / np.sum(P, axis=1, keepdims=True)
    ll = -np.log(P[np.arange(len(y)), y])
    return float(np.mean(ll))


def _accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y = np.asarray(y_true, dtype=int)
    p = np.asarray(y_pred, dtype=int)
    if len(y) == 0:
        return 0.0
    return float(np.mean(y == p))


# =========================
# train functions
# =========================

def _train_classifier_pwin(Xtr, ytr, Xva=None, yva=None, seed: int = 42) -> lgb.Booster:
    params = {
        "objective": "binary",
        "metric": ["auc", "binary_logloss"],
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "seed": seed,
        "verbosity": -1,
    }
    dtr = lgb.Dataset(Xtr, label=ytr, free_raw_data=False)
    valid_sets = [dtr]
    valid_names = ["train"]
    if Xva is not None and yva is not None and len(Xva) > 0:
        dva = lgb.Dataset(Xva, label=yva, free_raw_data=False)
        valid_sets.append(dva)
        valid_names.append("valid")

    booster = lgb.train(
        params,
        dtr,
        num_boost_round=2000,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=[lgb.early_stopping(stopping_rounds=80, verbose=False)],
    )
    return booster


def _train_regressor_ev(Xtr, ytr, Xva=None, yva=None, seed: int = 42) -> lgb.Booster:
    params = {
        "objective": "regression",
        "metric": ["l2", "l1"],
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "seed": seed,
        "verbosity": -1,
    }
    dtr = lgb.Dataset(Xtr, label=ytr, free_raw_data=False)
    valid_sets = [dtr]
    valid_names = ["train"]
    if Xva is not None and yva is not None and len(Xva) > 0:
        dva = lgb.Dataset(Xva, label=yva, free_raw_data=False)
        valid_sets.append(dva)
        valid_names.append("valid")

    booster = lgb.train(
        params,
        dtr,
        num_boost_round=2000,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=[lgb.early_stopping(stopping_rounds=80, verbose=False)],
    )
    return booster


def _train_regressor_hold_days(Xtr, ytr, Xva=None, yva=None, seed: int = 42) -> lgb.Booster:
    # 保有日数は外れ値が出るのでHuber寄りに（L1/quantileでもOKだが今回は安定優先）
    params = {
        "objective": "regression",
        "metric": ["l1"],
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "seed": seed,
        "verbosity": -1,
    }
    dtr = lgb.Dataset(Xtr, label=ytr, free_raw_data=False)
    valid_sets = [dtr]
    valid_names = ["train"]
    if Xva is not None and yva is not None and len(Xva) > 0:
        dva = lgb.Dataset(Xva, label=yva, free_raw_data=False)
        valid_sets.append(dva)
        valid_names.append("valid")

    booster = lgb.train(
        params,
        dtr,
        num_boost_round=2000,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=[lgb.early_stopping(stopping_rounds=80, verbose=False)],
    )
    return booster


def _train_multiclass_tp_first(Xtr, ytr, Xva=None, yva=None, seed: int = 42) -> lgb.Booster:
    params = {
        "objective": "multiclass",
        "num_class": 3,
        "metric": ["multi_logloss"],
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "seed": seed,
        "verbosity": -1,
    }
    dtr = lgb.Dataset(Xtr, label=ytr, free_raw_data=False)
    valid_sets = [dtr]
    valid_names = ["train"]
    if Xva is not None and yva is not None and len(Xva) > 0:
        dva = lgb.Dataset(Xva, label=yva, free_raw_data=False)
        valid_sets.append(dva)
        valid_names.append("valid")

    booster = lgb.train(
        params,
        dtr,
        num_boost_round=2000,
        valid_sets=valid_sets,
        valid_names=valid_names,
        callbacks=[lgb.early_stopping(stopping_rounds=80, verbose=False)],
    )
    return booster


class Command(BaseCommand):
    help = "LightGBMで p_win / EV / (任意)hold_days_pred / (任意)tp_first を学習して保存"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--valid-ratio", type=float, default=0.2)
        parser.add_argument("--out", type=str, default="", help="出力先（空なら media/aiapp/ml/models/<stamp>/）")
        parser.add_argument("--with-hold-days", action="store_true", help="hold_days_pred も学習する")
        parser.add_argument("--with-tp-first", action="store_true", help="tp_first/sl_first/none も学習する")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts) -> None:
        seed = int(opts.get("seed") or 42)
        valid_ratio = float(opts.get("valid_ratio") or 0.2)
        with_hold = bool(opts.get("with_hold_days") or False)
        with_tp_first = bool(opts.get("with_tp_first") or False)
        dry_run = bool(opts.get("dry_run") or False)

        df = _read_latest_train()
        df = _clean_df(df)

        feat_cols = _feature_cols(df)

        train_df, valid_df = _split_train_valid(df, valid_ratio=valid_ratio, seed=seed)

        Xtr = train_df[feat_cols]
        Xva = valid_df[feat_cols] if len(valid_df) > 0 else None

        # --------------------
        # p_win
        # --------------------
        ytr_win = _bin_y_win(train_df["y_label"])
        yva_win = _bin_y_win(valid_df["y_label"]) if Xva is not None else None

        # --------------------
        # EV
        # --------------------
        ytr_ev = _ev_target(train_df)
        yva_ev = _ev_target(valid_df) if Xva is not None else None

        self.stdout.write(self.style.SUCCESS("===== train_lgbm_models ====="))
        self.stdout.write(f"rows={len(df)} train={len(train_df)} valid={len(valid_df)} feat_cols={len(feat_cols)}")
        self.stdout.write(f"with_hold_days={with_hold} with_tp_first={with_tp_first} seed={seed}")

        if dry_run:
            self.stdout.write(self.style.WARNING("[train_lgbm_models] dry-run: training skipped."))
            return

        # train
        model_pwin = _train_classifier_pwin(Xtr, ytr_win, Xva, yva_win, seed=seed)
        model_ev = _train_regressor_ev(Xtr, ytr_ev, Xva, yva_ev, seed=seed)

        model_hold = None
        if with_hold and "y_hold_days" in df.columns:
            yd_tr = pd.to_numeric(train_df["y_hold_days"], errors="coerce").fillna(0).to_numpy(dtype=float)
            yd_va = pd.to_numeric(valid_df["y_hold_days"], errors="coerce").fillna(0).to_numpy(dtype=float) if Xva is not None else None
            model_hold = _train_regressor_hold_days(Xtr, yd_tr, Xva, yd_va, seed=seed)

        model_tp = None
        tp_map = _tp_first_map()
        if with_tp_first and "y_touch_tp_first" in df.columns:
            yt_tr = train_df["y_touch_tp_first"].astype(str).str.lower().str.strip().map(lambda x: tp_map.get(x, 0)).astype(int).to_numpy()
            yt_va = valid_df["y_touch_tp_first"].astype(str).str.lower().str.strip().map(lambda x: tp_map.get(x, 0)).astype(int).to_numpy() if Xva is not None else None
            model_tp = _train_multiclass_tp_first(Xtr, yt_tr, Xva, yt_va, seed=seed)

        # --------------------
        # metrics (valid)
        # --------------------
        metrics: Dict[str, Any] = {
            "valid_rows": int(len(valid_df)),
            "p_win": {},
            "ev": {},
            "hold_days_pred": {},
            "tp_first": {},
        }

        if Xva is not None and len(valid_df) > 0:
            # p_win
            pwin_pred = model_pwin.predict(Xva, num_iteration=getattr(model_pwin, "best_iteration", None))
            pwin_pred = np.asarray(pwin_pred, dtype=float)
            auc = _auc_roc(yva_win, pwin_pred) if yva_win is not None else None
            logloss = _binary_logloss(yva_win, pwin_pred) if yva_win is not None else None
            metrics["p_win"] = {
                "auc": float(auc) if auc is not None else None,
                "logloss": float(logloss) if logloss is not None else None,
            }

            # EV
            ev_pred = model_ev.predict(Xva, num_iteration=getattr(model_ev, "best_iteration", None))
            ev_pred = np.asarray(ev_pred, dtype=float)
            metrics["ev"] = {
                "rmse": _rmse(yva_ev, ev_pred) if yva_ev is not None else None,
                "mae": _mae(yva_ev, ev_pred) if yva_ev is not None else None,
                "target": "y_r if available else y_pl",
            }

            # hold_days_pred
            if model_hold is not None:
                yd_va = pd.to_numeric(valid_df["y_hold_days"], errors="coerce").fillna(0).to_numpy(dtype=float)
                hold_pred = model_hold.predict(Xva, num_iteration=getattr(model_hold, "best_iteration", None))
                hold_pred = np.asarray(hold_pred, dtype=float)
                metrics["hold_days_pred"] = {
                    "mae": _mae(yd_va, hold_pred),
                }

            # tp_first
            if model_tp is not None:
                yt_va = valid_df["y_touch_tp_first"].astype(str).str.lower().str.strip().map(lambda x: tp_map.get(x, 0)).astype(int).to_numpy()
                proba = model_tp.predict(Xva, num_iteration=getattr(model_tp, "best_iteration", None))
                proba = np.asarray(proba, dtype=float)
                pred_cls = np.argmax(proba, axis=1).astype(int) if proba.ndim == 2 else np.zeros(len(yt_va), dtype=int)
                metrics["tp_first"] = {
                    "accuracy": _accuracy(yt_va, pred_cls),
                    "logloss": _multiclass_logloss(yt_va, proba, num_class=3),
                    "label_map": tp_map,
                }
        else:
            # valid が無い場合
            metrics["note"] = "valid split is empty -> metrics skipped"

        # save
        out_opt = str(opts.get("out") or "").strip()
        if out_opt:
            out_dir = Path(out_opt)
        else:
            out_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "ml" / "models" / _now_stamp()
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "feature_cols.json").write_text(json.dumps(feat_cols, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        label_maps = {"tp_first_map": tp_map}
        (out_dir / "label_maps.json").write_text(json.dumps(label_maps, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        meta = {
            "created_at": datetime.now(JST).isoformat(),
            "rows": int(len(df)),
            "train_rows": int(len(train_df)),
            "valid_rows": int(len(valid_df)),
            "valid_ratio": float(valid_ratio),
            "seed": int(seed),
            "features": feat_cols,
            "targets": {
                "p_win": "win vs not_win",
                "ev": "y_r if available else y_pl",
                "hold_days_pred": bool(model_hold is not None),
                "tp_first": bool(model_tp is not None),
            },
            "best_iteration": {
                "p_win": int(getattr(model_pwin, "best_iteration", 0) or 0),
                "ev": int(getattr(model_ev, "best_iteration", 0) or 0),
                "hold_days": int(getattr(model_hold, "best_iteration", 0) or 0) if model_hold is not None else 0,
                "tp_first": int(getattr(model_tp, "best_iteration", 0) or 0) if model_tp is not None else 0,
            },
            "metrics": metrics,
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        model_pwin.save_model(str(out_dir / "model_pwin.txt"))
        model_ev.save_model(str(out_dir / "model_ev.txt"))
        if model_hold is not None:
            model_hold.save_model(str(out_dir / "model_hold_days.txt"))
        if model_tp is not None:
            model_tp.save_model(str(out_dir / "model_tp_first.txt"))

        # latest symlink-like（ファイルコピーで簡易運用）
        latest_dir = Path(settings.MEDIA_ROOT) / "aiapp" / "ml" / "models" / "latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        for name in ["meta.json", "feature_cols.json", "label_maps.json", "model_pwin.txt", "model_ev.txt", "model_hold_days.txt", "model_tp_first.txt"]:
            src = out_dir / name
            if src.exists():
                (latest_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(f"[train_lgbm_models] saved: {out_dir}"))
        self.stdout.write(self.style.SUCCESS(f"[train_lgbm_models] latest: {latest_dir}"))
        if metrics.get("valid_rows", 0) > 0:
            self.stdout.write(self.style.SUCCESS("[train_lgbm_models] metrics(valid): " + json.dumps(metrics, ensure_ascii=False)))