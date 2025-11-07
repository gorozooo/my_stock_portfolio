"""
aiapp.models.scoring
特徴量辞書から 0–100 の総合得点を算出する。

短期×攻め（デフォルト）重み:
- トレンド/EMA傾き          20
- 相対強度（10日）         22
- モメンタム(RSI/ROC)      18
- 出来高シグナル           16
- ボラ制御(ATR帯)          10
- 節目/ブレイク             10
- イベント減点（未使用）     0（将来拡張）

レジーム補正（任意）: trend/meanrev/defense を 0–100 として ±10% の微調整
"""

from __future__ import annotations
from typing import Optional, Dict

def _clip(v: Optional[float], lo: float, hi: float, default: float = 0.0) -> float:
    if v is None:
        return default
    return max(lo, min(hi, float(v)))

def _scale_pos(value: float, lo: float, hi: float) -> float:
    # lo〜hi を 0〜1 に線形スケール（上昇ほど良い）
    if hi - lo == 0:
        return 0.0
    return (value - lo) / (hi - lo)

def _scale_neg(value: float, lo: float, hi: float) -> float:
    # lo〜hi を 0〜1 に線形スケール（小さいほど良い）
    if hi - lo == 0:
        return 0.0
    return 1.0 - (value - lo) / (hi - lo)

def score_short_aggr(feat: Dict, regime: Dict | None = None) -> float:
    """
    短期×攻めの総合得点（0–100）
    feat: features.compute_features の返り値（辞書）
    regime: {"trend":0-100, "meanrev":0-100, "defense":0-100} 省略可
    """
    # 指標の正規化（0〜1）
    ema_slope = _scale_pos(_clip(feat.get("ema_slope"), -50, 50), -50, 50)
    rel_str   = _scale_pos(_clip(feat.get("rel_strength_10"), -10, 10), -10, 10)
    rsi       = _scale_pos(_clip(feat.get("rsi14"), 30, 70), 30, 70)     # 50中心に拡げる案も可
    roc       = _scale_pos(_clip(feat.get("roc10"), -10, 10), -10, 10)
    vol_sig   = _scale_pos(_clip(feat.get("vol_ma20_ratio"), 0.5, 2.0), 0.5, 2.0)
    atr_ctrl  = _scale_neg(_clip(feat.get("atr14"), 0, 15), 0, 15)       # ボラ小さいほど良い
    breakout  = 1.0 if feat.get("breakout_flag", 0) == 1 else 0.0

    # 重み
    w_trend, w_rel, w_mom, w_vol, w_atr, w_brk = 20, 22, 18, 16, 10, 10

    # レジーム補正（±10%）
    if regime:
        if regime.get("trend", 0) > 60:
            w_trend = int(w_trend * 1.1)
        if regime.get("defense", 0) > 60:
            w_atr = int(w_atr * 1.1)
        if regime.get("meanrev", 0) > 60:
            # 逆張り優勢時はブレイクの重みを少し下げる
            w_brk = int(w_brk * 0.9)

    total_w = w_trend + w_rel + w_mom + w_vol + w_atr + w_brk
    score01 = (
        ema_slope * w_trend +
        rel_str   * w_rel +
        (0.5*rsi + 0.5*roc) * w_mom +
        vol_sig   * w_vol +
        atr_ctrl  * w_atr +
        breakout  * w_brk
    ) / max(1, total_w)

    return float(round(score01 * 100.0, 2))
