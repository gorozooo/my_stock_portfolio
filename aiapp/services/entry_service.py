# aiapp/services/entry_service.py
# -*- coding: utf-8 -*-
"""
AI Picks 用の Entry / TP / SL を動的に計算するサービス

- 現状は「短期 × 攻め (short × aggressive)」モードをメインターゲット
- 入力は「終値(last) と ATR(ボラティリティ指標)」
- mode/horizon は将来拡張用（他モードでも動くように分岐は用意）
- 買い（ロング）前提のロジック（空売り対応は後で追加）

★今回の改修ポイント（重要）
- ML 推論の tp_first 確率（p_tp_first）を受け取れるようにして、
  その確率が低い（SL先になりやすい）ほど RR を上げる方向に TP/SL を自動調整する。
- やってることは「モデルに価格を当てさせる」のではなく、
  ATRベースの枠の中で係数を可変化して “勝てる形に寄せる”。

★追加（今回の依頼）
- shape（係数）を「simulate 側で吐ける」ように、係数を返す関数を追加する。
  compute_shape_coeffs() -> entry_k / rr_target / tp_k / sl_k
  これを compute_entry_tp_sl() も内部で利用して、価格計算と shape を一致させる。
"""

from __future__ import annotations
from typing import Any, Optional, Tuple, Dict

import math


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


def _rr_target_from_p_tp_first(p_tp_first: Optional[float]) -> float:
    """
    p(tp_first) に応じた目標RRを返す。
    - p が低い（SL先）ほど、RR を高くしないと期待値が成立しづらい。
    """
    if p_tp_first is None:
        return 1.0
    try:
        p = float(p_tp_first)
    except Exception:
        return 1.0
    if not math.isfinite(p):
        return 1.0

    # ここは運用しながら後で詰められる
    if p <= 0.35:
        return 1.6
    if p <= 0.55:
        return 1.25
    return 1.05


def compute_shape_coeffs(
    last: float,
    atr: float,
    mode: str = "aggressive",
    horizon: str = "short",
    *,
    p_tp_first: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """
    Entry/TP/SL の「形（係数）」を返す。

    返すもの
    --------
    entry_k : Entry = last + entry_k * ATR
    rr_target : 目標RR（設計RR）
    tp_k : TP = entry + tp_k * ATR
    sl_k : SL = entry - sl_k * ATR

    ★B仕様（重要）
    - p_tp_first が None（= ml_ok=False 等）でも shape は必ず返す
      → rr_target は 1.0（デフォルト）になり、係数は素の設計値で埋まる。
    """
    last_v = _safe_float(last)
    atr_v = _safe_float(atr)

    # 防御：値が変でも落ちないように（shapeも None で返す）
    if last_v is None or atr_v is None or atr_v <= 0 or last_v <= 0:
        return {
            "entry_k": None,
            "rr_target": None,
            "tp_k": None,
            "sl_k": None,
        }

    mode = (mode or "aggressive").lower()
    horizon = (horizon or "short").lower()

    # --- ① ボラティリティ（%）をざっくり見る ---------------------------
    vol_pct = atr_v / last_v * 100.0
    vol_pct = _clamp(vol_pct, 0.1, 20.0)

    if vol_pct < 2.0:
        vol_zone = "calm"
    elif vol_pct < 7.0:
        vol_zone = "normal"
    else:
        vol_zone = "wild"

    # --- ② ベース係数（horizon / mode） --------------------------------
    if horizon == "short":
        if mode == "aggressive":
            base_entry_k = 0.05
            base_tp_k = 0.80
            base_sl_k = 0.60
        elif mode == "defensive":
            base_entry_k = 0.02
            base_tp_k = 0.60
            base_sl_k = 0.40
        else:  # normal
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
    else:  # long
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

    # --- ③ Entry調整（高値掴み回避） -----------------------------------
    if horizon == "short" and mode == "aggressive":
        if vol_zone == "calm":
            entry_k = base_entry_k * 1.2
        elif vol_zone == "normal":
            entry_k = base_entry_k
        else:  # wild
            entry_k = -0.20
    else:
        entry_k = base_entry_k

    # ★ ML で tp_first が弱いなら、飛びつきをもう一段抑える（押し目寄せ）
    if horizon == "short" and mode == "aggressive" and p_tp_first is not None:
        p = _safe_float(p_tp_first)
        if p is not None:
            if p <= 0.35:
                entry_k = min(entry_k, -0.10)
            elif p <= 0.45:
                entry_k = min(entry_k, 0.00)

    # --- ④ TP/SL のボラ調整 --------------------------------------------
    vol_scale = _clamp(0.9 + (vol_pct - 2.0) * 0.03, 0.7, 1.3)

    tp_k = base_tp_k * vol_scale
    sl_k = base_sl_k * vol_scale

    # safety
    sl_k = _clamp(sl_k, 0.2, tp_k * 1.2)

    # =========================================================
    # ★ ⑤ ML 確率で RR ターゲット制御（今回の核）
    # =========================================================
    if horizon == "short" and mode == "aggressive":
        rr_target = _rr_target_from_p_tp_first(_safe_float(p_tp_first))
        sl_k = _clamp(sl_k, 0.35, 1.80)
        tp_k_target = rr_target * sl_k
        tp_k = max(tp_k, tp_k_target)
        tp_k = _clamp(tp_k, 0.55, 3.00)
    else:
        rr_target = 1.0

    return {
        "entry_k": float(entry_k),
        "rr_target": float(rr_target),
        "tp_k": float(tp_k),
        "sl_k": float(sl_k),
    }


def compute_entry_tp_sl(
    last: float,
    atr: float,
    mode: str = "aggressive",
    horizon: str = "short",
    **kwargs,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Entry / TP / SL をまとめて計算して返す。

    kwargs（今回から使う）
    ----------------------
    p_tp_first : Optional[float]
        ML の tp_first 確率（0..1）。低いほど “SL先になりやすい” と見てRRを引き上げる。
        picks_build 側で ml_tp_first_probs['tp_first'] を渡す。
    """
    last_v = _safe_float(last)
    atr_v = _safe_float(atr)

    # 防御：値が変でも落ちないように
    if last_v is None or atr_v is None or atr_v <= 0 or last_v <= 0:
        return None, None, None

    mode = (mode or "aggressive").lower()
    horizon = (horizon or "short").lower()

    # ML 由来（無ければ None）
    p_tp_first = _safe_float(kwargs.get("p_tp_first"))

    # shape（係数）を同一ロジックで作る（価格計算と一致）
    shape = compute_shape_coeffs(
        last=last_v,
        atr=atr_v,
        mode=mode,
        horizon=horizon,
        p_tp_first=p_tp_first,
    )
    entry_k = shape.get("entry_k")
    tp_k = shape.get("tp_k")
    sl_k = shape.get("sl_k")

    if entry_k is None or tp_k is None or sl_k is None:
        return None, None, None

    # --- 実際の価格を計算 --------------------------------------------
    entry = last_v + float(entry_k) * atr_v
    tp = entry + float(tp_k) * atr_v
    sl = entry - float(sl_k) * atr_v

    # 価格がマイナスにならないよう最低0.1でクリップ
    if entry <= 0 or tp <= 0 or sl <= 0:
        entry = max(entry, 0.1)
        tp = max(tp, 0.1)
        sl = max(sl, 0.1)

    return float(entry), float(tp), float(sl)