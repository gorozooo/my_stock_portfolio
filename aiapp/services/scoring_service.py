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

さらに本格版として、
  - regime（MacroRegimeSnapshot 等）をオプション引数で受け取り、
  - RISK_ON / RISK_OFF、JP/USトレンド、為替、ボラを
    「スコア計算そのもの」に掛け合わせる。

score_sample(feat_df, regime=None) -> 0..1
stars_from_score(score01) -> 1..5
"""

from __future__ import annotations
from typing import Optional, Any, Dict

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
        return 1.0 / 2.0


def _nz(x: float, default: float = 0.0) -> float:
    return default if (x is None or not np.isfinite(x)) else float(x)


# ====== レジームコンテキスト（本格版） ======

def _extract_regime_ctx(regime: Optional[object]) -> Dict[str, Optional[str]]:
    """
    MacroRegimeSnapshot / dict / None のどれでも受け取り、
    {regime_label, jp_trend_label, us_trend_label, fx_trend_label, vol_label}
    を大文字ストリング or None に正規化する。
    """
    fields = ["regime_label", "jp_trend_label", "us_trend_label", "fx_trend_label", "vol_label"]
    ctx: Dict[str, Optional[str]] = {k: None for k in fields}

    if regime is None:
        return ctx

    for k in fields:
        v: Optional[str] = None
        try:
            if isinstance(regime, dict):
                v = regime.get(k)  # type: ignore[arg-type]
            else:
                v = getattr(regime, k, None)
        except Exception:
            v = None
        if isinstance(v, str):
            ctx[k] = v.strip().upper()
        elif v is not None:
            ctx[k] = str(v).strip().upper()
    return ctx


def _regime_multiplier(ctx: Dict[str, Optional[str]]) -> float:
    """
    レジームに応じて 0.7〜1.3 程度の倍率を返す。
    - RISK_ON: 少し攻め寄せ
    - RISK_OFF: 明確に守り寄せ
    - 日本株/米国株トレンド: UP でわずかに加点, DOWN で減点
    - 為替: YEN_WEAK で株にやや追い風, YEN_STRONG で逆
    - ボラ: CALM / ELEVATED / HIGH でリスクコントロール
    """
    base = 1.0

    regime_label = (ctx.get("regime_label") or "").upper()
    jp = (ctx.get("jp_trend_label") or "").upper()
    us = (ctx.get("us_trend_label") or "").upper()
    fx = (ctx.get("fx_trend_label") or "").upper()
    vol = (ctx.get("vol_label") or "").upper()

    # --- 総合レジーム ---
    if regime_label == "RISK_ON":
        base += 0.12
    elif regime_label == "RISK_OFF":
        base -= 0.15
    elif regime_label == "NEUTRAL":
        base += 0.0

    # --- 日本株・米国株トレンド ---
    if jp == "UP":
        base += 0.05
    elif jp == "DOWN":
        base -= 0.05

    if us == "UP":
        base += 0.03
    elif us == "DOWN":
        base -= 0.03

    # --- 為替（簡易：YEN_WEAK=株追い風, YEN_STRONG=逆風） ---
    if fx in ("YEN_WEAK", "WEAK_YEN"):
        base += 0.03
    elif fx in ("YEN_STRONG", "STRONG_YEN"):
        base -= 0.03

    # --- ボラティリティ ---
    if vol == "CALM":
        base += 0.04
    elif vol == "ELEVATED":
        base -= 0.03
    elif vol == "HIGH":
        base -= 0.07

    # RISK_OFF × HIGH ボラ のときは、さらに一段絞る
    if regime_label == "RISK_OFF" and vol == "HIGH":
        base -= 0.05

    # 暴れ過ぎないようクランプ（0.7〜1.3倍）
    base = max(0.7, min(1.3, base))
    return float(base)


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

def score_sample(feat: pd.DataFrame, regime: Optional[object] = None) -> float:
    """
    総合得点の確定ロジック（0..1）。“常に連続値”になるよう調整。
    regime を渡した場合は、相場レジーム（RISK_ON/OFF, JP/US, FX, VOL）を
    スコア計算そのものに掛け合わせる。

    - feat: features.make_features() からの DataFrame
    - regime: MacroRegimeSnapshot インスタンス or dict or None
    """
    if feat is None or len(feat) == 0:
        return 0.0

    # --- テクニカル側の素点 ---
    trend = _block_trend(feat)          # 0..1
    mom = _block_momentum(feat)         # 0..1
    volu = _block_volume(feat)          # 0..1
    vctrl = _block_vol_control(feat)    # 0..1
    sd = _block_supply_demand(feat)     # 0..1
    adj = _event_adj(feat)              # -0.02..+0.02

    # 短期×攻めの仮本番（FULL）
    base_score = (
        0.34 * trend +
        0.28 * mom +
        0.14 * volu +
        0.14 * vctrl +
        0.08 * sd
    )
    base_score = max(0.0, min(1.0, base_score))

    # --- マクロレジームを“本体ロジック”に織り込む ---
    ctx = _extract_regime_ctx(regime)
    mul = _regime_multiplier(ctx)
    score = base_score * mul

    # GCROSS/DCROSS 調整を最後に足し合わせ
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