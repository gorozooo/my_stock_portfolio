# aiapp/services/entry_service.py
# -*- coding: utf-8 -*-
"""
AI Picks 用の Entry / TP / SL を動的に計算するサービス（プロ仕様）

方針（ここが重要）：
- Entry/TP/SL は「ML予測 + テクニカル位置情報」で自動調整する
- MLが無い/欠損のときは従来のATRベースでフォールバック（落とさない）
- テクニカル（RSIなど）を「重み最適化の主役」にしない
  → MLが出した “勝ちやすさ/負けやすさ/期待値/先にTPかSLか” を主役にする
  → テクニカルは「どこに置くか（VWAP寄せ/押し目/追随）」の補助に使う

picks_build からの呼び出し例（互換）：
    e, t, s = compute_entry_tp_sl(last, atr, mode="aggressive", horizon="short")

MLを使う場合（推奨）：
    e, t, s = compute_entry_tp_sl(
        last, atr,
        mode="aggressive", horizon="short",
        feat_df=feat,  # 特徴量DataFrame
        ml_ev=ml_ev,
        ml_p_win=ml_p_win,
        ml_hold_days_pred=ml_hold_days_pred,
        ml_tp_first_probs=ml_tp_first_probs,  # {'tp_first':..., 'sl_first':..., 'none':...}
        side="BUY",  # 将来拡張（今はBUYメイン）
    )
"""

from __future__ import annotations
from typing import Any, Dict, Optional, Tuple

import math


# =========================================================
# utils
# =========================================================

def _safe_float(x: Any) -> Optional[float]:
    """どんな入力でも float or None に丸める"""
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    """簡易 clamp"""
    return max(lo, min(hi, x))


def _get_last_from_feat(feat_df: Any, key: str) -> Optional[float]:
    """
    features DataFrame から末尾値を取る（無ければNone）
    """
    try:
        if feat_df is None:
            return None
        if key not in feat_df.columns:
            return None
        v = feat_df[key].iloc[-1]
        return _safe_float(v)
    except Exception:
        return None


def _normalize_probs(d: Any) -> Dict[str, float]:
    """
    tp_first/sl_first/none の確率辞書を正規化する。
    """
    out = {"tp_first": 0.0, "sl_first": 0.0, "none": 0.0}
    if not isinstance(d, dict):
        return out
    for k in ("tp_first", "sl_first", "none"):
        v = _safe_float(d.get(k))
        if v is None:
            v = 0.0
        out[k] = float(max(0.0, min(1.0, v)))
    # 合計が0ならそのまま
    s = out["tp_first"] + out["sl_first"] + out["none"]
    if s <= 0:
        return out
    # 過剰にズレてても一応正規化（軽く）
    out = {k: float(v / s) for k, v in out.items()}
    return out


# =========================================================
# Core: ML最適化（主役）
# =========================================================

def optimize_entry_tp_sl(
    *,
    last: float,
    atr: float,
    mode: str,
    horizon: str,
    feat_df: Any = None,
    ml_ev: Optional[float] = None,
    ml_p_win: Optional[float] = None,
    ml_hold_days_pred: Optional[float] = None,
    ml_tp_first_probs: Any = None,
    side: str = "BUY",
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    ML予測を使って Entry/TP/SL を自動調整して返す。

    重要：
    - ここは “重み最適化” ではなく、まず「実運用で効く」ルールを固定する段階
    - 後でログ（採用/不採用、結果）を学習して係数を更新していく前提
    """
    last_v = _safe_float(last)
    atr_v = _safe_float(atr)

    if last_v is None or atr_v is None or atr_v <= 0 or last_v <= 0:
        return None, None, None

    side = (side or "BUY").upper()
    mode = (mode or "aggressive").lower()
    horizon = (horizon or "short").lower()

    # --- 位置情報（補助） ---
    vwap = _get_last_from_feat(feat_df, "VWAP")
    # 直近高値（追随に使える）
    # make_features側に HIGH_20 / HIGH_10 等があるなら拾う。無ければNone。
    high_20 = _get_last_from_feat(feat_df, "HIGH_20")
    high_10 = _get_last_from_feat(feat_df, "HIGH_10")

    # --- ML入力（主役） ---
    ev = _safe_float(ml_ev)
    pwin = _safe_float(ml_p_win)
    hold = _safe_float(ml_hold_days_pred)

    probs = _normalize_probs(ml_tp_first_probs)
    p_tp = float(probs.get("tp_first", 0.0))
    p_sl = float(probs.get("sl_first", 0.0))

    # 欠損時の保守
    if ev is None:
        ev = 0.85  # “普通”の初期値（変に攻めない）
    if pwin is None:
        pwin = 0.55
    if hold is None:
        hold = 6.0

    # -----------------------------------------------------
    # 1) Entry方式の自動選択（追随 / 押し目 / VWAP寄せ）
    # -----------------------------------------------------
    # 追随：期待値が高く、TP先確率が優勢
    use_breakout = (ev >= 0.95 and p_tp >= 0.55)
    # 押し目：SL先が濃い（先に逆行しやすい）
    use_pullback = (p_sl >= 0.55)

    # Entry係数（ATR基準）
    # 追随は上で拾う、押し目は下で待つ、VWAPは寄せる
    if use_breakout and not use_pullback:
        entry_mode = "breakout"
        # 高値ブレイクが取れるならブレイク基準、それ以外はlast基準
        ref = None
        if high_10 is not None and high_10 > 0:
            ref = high_10
        elif high_20 is not None and high_20 > 0:
            ref = high_20
        else:
            ref = last_v

        # 追随は「ちょい上」：ATRの 0.10〜0.25 くらい
        a = _clamp(0.10 + 0.12 * (ev - 0.95) + 0.10 * (p_tp - 0.55), 0.10, 0.25)
        entry = float(ref + a * atr_v)

    elif use_pullback:
        entry_mode = "pullback"
        # 押し目は下：ATRの 0.20〜0.50
        b = _clamp(0.20 + 0.35 * (p_sl - 0.55), 0.20, 0.50)
        entry = float(last_v - b * atr_v)

        # VWAPが近ければ “VWAP寄せ” を優先（極端に下で待たない）
        if vwap is not None and vwap > 0:
            # lastとVWAPが近いならVWAPを参照
            gap = abs(last_v - vwap) / last_v
            if gap <= 0.01:  # 1%以内ならVWAP寄せにする
                entry_mode = "vwap_pullback"
                entry = float(vwap - 0.08 * atr_v)

    else:
        # 中立：VWAPがあれば寄せ、無ければ軽い追随
        if vwap is not None and vwap > 0:
            entry_mode = "vwap"
            # VWAP±小さく（0.05〜0.10ATR）
            small = _clamp(0.05 + 0.05 * (p_tp - p_sl), 0.05, 0.10)
            # 上向き寄り/下向き寄りは確率差で決める
            sign = 1.0 if (p_tp >= p_sl) else -1.0
            entry = float(vwap + sign * small * atr_v)
        else:
            entry_mode = "neutral"
            entry = float(last_v + 0.05 * atr_v)

    # safety: entryが変な値にならない
    if entry <= 0:
        entry = max(0.1, last_v)

    # -----------------------------------------------------
    # 2) TP/SL距離（ATR倍率）を自動調整
    # -----------------------------------------------------
    # ベースは short/aggressive の “初期値” を置き、MLで動かす
    # ※ horizon/mode でベースを切替（将来拡張）
    if horizon == "short":
        base_sl = 1.2
        base_tp = 1.2
    elif horizon == "mid":
        base_sl = 1.5
        base_tp = 1.6
    else:
        base_sl = 1.8
        base_tp = 2.0

    if mode == "defensive":
        base_sl *= 1.10
        base_tp *= 0.90
    elif mode == "aggressive":
        base_sl *= 0.95
        base_tp *= 1.05

    # SL：SL先確率が高いほど広げる（ノイズで刈られない）
    k_sl = base_sl + 0.8 * p_sl
    k_sl = _clamp(k_sl, 1.0, 2.4)

    # TP：期待値が高いほど伸ばす + TP先が濃いほど伸ばす
    k_tp = base_tp + 1.4 * (ev - 0.80) + 0.6 * p_tp
    k_tp = _clamp(k_tp, 1.0, 3.0)

    # hold_days_predで微調整
    # 短い→近く、長い→伸ばす（特にTP）
    if hold <= 5.0:
        k_tp *= 0.92
        k_sl *= 0.95
    elif hold >= 12.0:
        k_tp *= 1.08
        k_sl *= 1.04

    k_tp = _clamp(k_tp, 1.0, 3.2)
    k_sl = _clamp(k_sl, 1.0, 2.6)

    # -----------------------------------------------------
    # 3) 実値計算（BUY/SELL 対応を崩さない）
    # -----------------------------------------------------
    if side == "SELL":
        # 逆（ショート）想定：TPは下、SLは上
        tp = float(entry - k_tp * atr_v)
        sl = float(entry + k_sl * atr_v)
    else:
        tp = float(entry + k_tp * atr_v)
        sl = float(entry - k_sl * atr_v)

    # safety: 価格がマイナスにならない
    entry = max(0.1, float(entry))
    tp = max(0.1, float(tp))
    sl = max(0.1, float(sl))

    # safety: TP/SLがentryを跨いで崩れていたら矯正
    if side == "SELL":
        # SELL: tp < entry < sl
        if not (tp < entry < sl):
            tp = min(tp, entry * 0.99)
            sl = max(sl, entry * 1.01)
    else:
        # BUY: sl < entry < tp
        if not (sl < entry < tp):
            sl = min(sl, entry * 0.99)
            tp = max(tp, entry * 1.01)

    return float(entry), float(tp), float(sl)


# =========================================================
# Backward compatible wrapper
# =========================================================

def compute_entry_tp_sl(
    last: float,
    atr: float,
    mode: str = "aggressive",
    horizon: str = "short",
    **kwargs,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Entry / TP / SL をまとめて計算して返す（互換API）。

    MLパラメータが渡されている場合は optimize_entry_tp_sl を優先。
    無い場合は従来のATRベース（落とさない）。
    """
    last_v = _safe_float(last)
    atr_v = _safe_float(atr)

    if last_v is None or atr_v is None or atr_v <= 0 or last_v <= 0:
        return None, None, None

    mode = (mode or "aggressive").lower()
    horizon = (horizon or "short").lower()

    # --- MLが揃っていれば最適化を使う（主役） ---
    has_ml = (
        ("ml_ev" in kwargs)
        or ("ml_p_win" in kwargs)
        or ("ml_tp_first_probs" in kwargs)
        or ("ml_hold_days_pred" in kwargs)
    )

    if has_ml:
        try:
            return optimize_entry_tp_sl(
                last=last_v,
                atr=atr_v,
                mode=mode,
                horizon=horizon,
                feat_df=kwargs.get("feat_df"),
                ml_ev=kwargs.get("ml_ev"),
                ml_p_win=kwargs.get("ml_p_win"),
                ml_hold_days_pred=kwargs.get("ml_hold_days_pred"),
                ml_tp_first_probs=kwargs.get("ml_tp_first_probs"),
                side=kwargs.get("side", "BUY"),
            )
        except Exception:
            # 最適化が落ちても従来式で返す（運用を止めない）
            pass

    # --- 従来のATRベース（フォールバック） ---
    vol_pct = atr_v / last_v * 100.0
    vol_pct = _clamp(vol_pct, 0.1, 20.0)

    if vol_pct < 2.0:
        vol_zone = "calm"
    elif vol_pct < 7.0:
        vol_zone = "normal"
    else:
        vol_zone = "wild"

    # ベース係数
    if horizon == "short":
        if mode == "aggressive":
            base_entry_k = 0.05
            base_tp_k = 0.80
            base_sl_k = 0.60
        elif mode == "defensive":
            base_entry_k = 0.02
            base_tp_k = 0.60
            base_sl_k = 0.40
        else:
            base_entry_k = 0.03
            base_tp_k = 0.70
            base_sl_k = 0.50
    elif horizon == "mid":
        if mode == "aggressive":
            base_entry_k = 0.03
            base_tp_k = 1.20
            base_sl_k = 0.80
        elif mode == "defensive":
            base_entry_k = 0.01
            base_tp_k = 0.80
            base_sl_k = 0.50
        else:
            base_entry_k = 0.02
            base_tp_k = 1.00
            base_sl_k = 0.66
    else:
        if mode == "aggressive":
            base_entry_k = 0.02
            base_tp_k = 1.80
            base_sl_k = 0.80
        elif mode == "defensive":
            base_entry_k = 0.00
            base_tp_k = 1.20
            base_sl_k = 0.50
        else:
            base_entry_k = 0.01
            base_tp_k = 1.50
            base_sl_k = 0.66

    if horizon == "short" and mode == "aggressive":
        if vol_zone == "calm":
            entry_k = base_entry_k * 1.2
        elif vol_zone == "normal":
            entry_k = base_entry_k
        else:
            entry_k = -0.20
    else:
        entry_k = base_entry_k

    vol_scale = _clamp(0.9 + (vol_pct - 2.0) * 0.03, 0.7, 1.3)
    tp_k = base_tp_k * vol_scale
    sl_k = base_sl_k * vol_scale
    sl_k = _clamp(sl_k, 0.2, tp_k * 1.2)

    entry = last_v + entry_k * atr_v
    tp = entry + tp_k * atr_v
    sl = entry - sl_k * atr_v

    if entry <= 0 or tp <= 0 or sl <= 0:
        entry = max(entry, 0.1)
        tp = max(tp, 0.1)
        sl = max(sl, 0.1)

    return float(entry), float(tp), float(sl)