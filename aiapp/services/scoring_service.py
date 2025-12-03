# -*- coding: utf-8 -*-
"""
scoring_service.py
本番用: 総合得点(0..1) と ⭐️信頼度(1..5) を一貫ロジックで算出。

設計ポイント
- 入力は features.make_features() が返す DataFrame（欠損を含んでもOK）
- 0点/⭐️固定を避けるため、正規化→シグモイド→加重和で“連続値”を合成
- 列が無い/NaN でも安全に処理（内部で0寄せ or 中立値）
- ⭐️は固定閾値（学習で変えない限り変動しない）

特徴量バスケット（ある分だけ使う）
  Trend:     SLOPE_5, SLOPE_20
  Momentum:  RET_5, RET_20, RSI14（RSIは50を中立化）
  Volume:    Volume / MA20（MA20が無ければ無効）
  VolCtrl:   ATR14 を “低い方が扱いやすい” として正規化
  Supply/Demand (簡易): VWAP_GAP_PCT（VWAP付近なら中立〜やや好意）
  Event Penalty: 直近のDCROSSは微減点、直近のGCROSSは微加点

score_sample(feat_df) -> 0..1
stars_from_score(score01) -> 1..5
"""

from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd


# ====== 安全ユーティリティ ======

def _safe_series(x) -> pd.Series:
    if x is None:
        return pd.Series(dtype="float64")
    if isinstance(x, pd.Series):
        return x.astype("float64")
    if isinstance(x, pd.DataFrame):
        return x.iloc[:, -1].astype("float64") if x.shape[1] else pd.Series(dtype="float64")
    try:
        arr = np.asarray(x, dtype="float64")
        if arr.ndim == 0:
            return pd.Series([float(arr)], dtype="float64")
        return pd.Series(arr, dtype="float64")
    except Exception:
        return pd.Series(dtype="float64")


def _last(s: pd.Series) -> float:
    s = _safe_series(s).dropna()
    return float(s.iloc[-1]) if len(s) else float("nan")


def _zscore_last(s: pd.Series) -> float:
    s = _safe_series(s).dropna()
    if len(s) < 3:
        return float("nan")
    m = float(s.mean())
    sd = float(s.std(ddof=0))
    if not np.isfinite(sd) or sd == 0.0:
        return 0.0
    return float((s.iloc[-1] - m) / sd)


def _sig(x: float) -> float:
    try:
        return 1.0 / (1.0 + np.exp(-float(x)))
    except Exception:
        return 0.5


def _nz(x: float, default: float = 0.0) -> float:
    return default if (x is None or not np.isfinite(x)) else float(x)


# ====== コア指標の取り出し（ある分だけ使う） ======

def _block_trend(feat: pd.DataFrame) -> float:
    s5 = _zscore_last(feat.get("SLOPE_5"))
    s20 = _zscore_last(feat.get("SLOPE_20"))
    # 短期をやや重め
    comp = 0.6 * _sig(_nz(s5)) + 0.4 * _sig(_nz(s20))
    return comp  # 0..1


def _block_momentum(feat: pd.DataFrame) -> float:
    r5 = _zscore_last(feat.get("RET_5"))
    r20 = _zscore_last(feat.get("RET_20"))
    rsi = _last(feat.get("RSI14"))
    # RSIは 50 を中立にしてスケール
    rsi_c = np.nan if not np.isfinite(rsi) else (rsi - 50.0) / 10.0
    comp = 0.45 * _sig(_nz(r5)) + 0.35 * _sig(_nz(r20)) + 0.20 * _sig(_nz(rsi_c))
    return comp  # 0..1


def _block_volume(feat: pd.DataFrame) -> float:
    vol = _last(feat.get("Volume"))
    ma20 = _last(feat.get("MA20"))
    if not np.isfinite(vol) or not np.isfinite(ma20) or ma20 <= 0:
        return 0.5  # 情報なし=中立
    ratio = (vol / ma20) - 1.0       # 0 近辺が中立（※MA20は価格ベースなので“目安”扱い）
    return _sig(ratio)               # 0..1（~1で出来高相対強）


def _block_vol_control(feat: pd.DataFrame) -> float:
    atr = _last(feat.get("ATR14"))
    if not np.isfinite(atr) or atr <= 0:
        return 0.5
    # “扱いやすさ”を 1/(1 + z) っぽく変換（ATRが小さいほど↑）
    # ここでは ATR を対数正規化してから符号反転してシグモイド
    s = _zscore_last(np.log(_safe_series(feat.get("ATR14")).replace(0, np.nan)))
    return _sig(-_nz(s))  # 低ボラをやや優遇


def _block_supply_demand(feat: pd.DataFrame) -> float:
    vgap = _last(feat.get("VWAP_GAP_PCT"))
    if not np.isfinite(vgap):
        return 0.5
    # VWAP近接は中立〜少し好意。±1%以内は0.55、上離れ/下離れ大きいと中立へ
    if abs(vgap) <= 1.0:
        return 0.55
    if abs(vgap) <= 3.0:
        return 0.52
    return 0.5


def _event_adj(feat: pd.DataFrame) -> float:
    # 直近GCROSS/DCROSSで微調整（±0.02程度）
    g = _last(feat.get("GCROSS"))
    d = _last(feat.get("DCROSS"))
    adj = 0.0
    if np.isfinite(g) and g > 0:
        adj += 0.02
    if np.isfinite(d) and d > 0:
        adj -= 0.02
    return adj


# ====== 公開API ======

def score_sample(feat: pd.DataFrame) -> float:
    """
    総合得点の確定ロジック（0..1）。“常に連続値”になるよう調整。
    """
    if feat is None or len(feat) == 0:
        return 0.0

    trend = _block_trend(feat)          # 0..1
    mom = _block_momentum(feat)         # 0..1
    volu = _block_volume(feat)          # 0..1
    vctrl = _block_vol_control(feat)    # 0..1
    sd = _block_supply_demand(feat)     # 0..1
    adj = _event_adj(feat)              # -0.02..+0.02

    # 重みは短期×攻めの仮本番（FULL）
    score = (
        0.34 * trend +
        0.28 * mom +
        0.14 * volu +
        0.14 * vctrl +
        0.08 * sd
    )
    score = max(0.0, min(1.0, score + adj))
    return float(score)


def stars_from_score(score01: float) -> int:
    """
    ⭐️は固定し、毎回同じスコア→同じ⭐️になる（ぶれない）。
    """
    s = _nz(score01, 0.0)
    if s < 0.20:
        return 1
    if s < 0.40:
        return 2
    if s < 0.60:
        return 3
    if s < 0.80:
        return 4
    return 5