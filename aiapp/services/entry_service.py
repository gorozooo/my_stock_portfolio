# aiapp/services/entry_service.py
# -*- coding: utf-8 -*-
"""
AI Picks 用の Entry / TP / SL を動的に計算するサービス

- 現状は「短期 × 攻め (short × aggressive)」モードをメインターゲット
- 入力は「終値(last) と ATR(ボラティリティ指標)」
- mode/horizon は将来拡張用（他モードでも動くように分岐は用意）
- 買い（ロング）前提のロジック（空売り対応は後で追加）

picks_build からは以下のように呼び出される想定：

    from aiapp.services.entry_service import compute_entry_tp_sl

    e, t, s = compute_entry_tp_sl(
        last,
        atr,
        mode="aggressive",
        horizon="short",
        # 将来: feat=特徴量DataFrame などを渡せるようにしてある（kwargs）
    )

"""

from __future__ import annotations
from typing import Any, Optional, Tuple

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


def compute_entry_tp_sl(
    last: float,
    atr: float,
    mode: str = "aggressive",
    horizon: str = "short",
    **kwargs,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Entry / TP / SL をまとめて計算して返す。

    Parameters
    ----------
    last : float
        現在値（終値）
    atr : float
        ATR（14 など）ボラティリティ指標
    mode : str
        "aggressive" / "defensive" / "normal" など（将来拡張）
    horizon : str
        "short" / "mid" / "long" など（将来拡張）
    kwargs :
        将来用の拡張引数（feat=特徴量DataFrame など）
        ※ 受け取れるようにしておくだけで、今は使わなくてもエラーにしない

    Returns
    -------
    (entry, tp, sl) : Tuple[float | None, float | None, float | None]
    """

    last_v = _safe_float(last)
    atr_v = _safe_float(atr)

    # 防御：値が変でも落ちないように
    if last_v is None or atr_v is None or atr_v <= 0 or last_v <= 0:
        return None, None, None

    mode = (mode or "aggressive").lower()
    horizon = (horizon or "short").lower()

    # --- ① ボラティリティ（%）をざっくり見る ---------------------------
    #    「過去ボラティリティに応じて Entry/TP/SL の距離を変える」ための基準
    vol_pct = atr_v / last_v * 100.0
    # あまり極端な値になりすぎても意味が薄いのでざっくり clamp
    vol_pct = _clamp(vol_pct, 0.1, 20.0)

    # ここで「静かな銘柄 / ほどほど / 激しい銘柄」をざっくり分類
    if vol_pct < 2.0:
        vol_zone = "calm"       # 静か
    elif vol_pct < 7.0:
        vol_zone = "normal"     # ふつう
    else:
        vol_zone = "wild"       # 激しい

    # --- ② ベースとなる係数を horizon / mode で決める -------------------
    # ここが「固定式 → モード別の動的式」に変わった部分
    if horizon == "short":
        if mode == "aggressive":
            base_entry_k = 0.05   # 元々の +0.05 ATR をベース
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
        # 中期は少し広めに
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

    # --- ③ 「高値掴みしない」ための Entry 調整 -------------------------
    # vol_zone を使って、短期×攻めモードでは以下のように振る舞う：
    #
    #  - calm   : あまり動かない → 多少追いかけてもOK → entry をやや上に
    #  - normal : 以前のロジックに近い
    #  - wild   : 激しい → 飛びつき禁止 → 一段押しを待つ（last より下に置く）
    #
    # ※ 本当は RSI や 直近リターンで「過熱感」を見るのがベストだが、
    #    現段階では picks_build から last/ATR しか渡ってこないため、
    #    まずは ATR ベースで「静か / 普通 / 激しい」を分けて制御している。
    #    後で feat(DataFrame) を渡すようにすれば、RSI やモメンタムも組み込める。
    if horizon == "short" and mode == "aggressive":
        if vol_zone == "calm":
            # あまり動かない銘柄 → 多少上に置いても高値掴みリスクは小さい
            entry_k = base_entry_k * 1.2  # 0.06ATR くらい
        elif vol_zone == "normal":
            # 従来どおり
            entry_k = base_entry_k       # 0.05ATR
        else:  # wild
            # 激しく動く銘柄 → 一段押しを待つため「last より下」に置く
            # ex) last - 0.2 ATR
            entry_k = -0.20
    else:
        # 他モードは今のところベース値のまま
        entry_k = base_entry_k

    # --- ④ TP / SL をボラティリティに応じて微調整 -----------------------
    # ボラが高いほど TP/SL を少し広げる（ただしやりすぎない）
    # 例：vol_pct = 2 → scale ≒ 0.9 / vol_pct = 10 → scale ≒ 1.2
    vol_scale = _clamp(0.9 + (vol_pct - 2.0) * 0.03, 0.7, 1.3)

    tp_k = base_tp_k * vol_scale
    sl_k = base_sl_k * vol_scale

    # safety: SL が TP を超える極端なケースは丸める
    sl_k = _clamp(sl_k, 0.2, tp_k * 1.2)

    # --- ⑤ 実際の価格を計算 ---------------------------------------------
    entry = last_v + entry_k * atr_v
    tp = entry + tp_k * atr_v
    sl = entry - sl_k * atr_v

    # 価格がマイナスにならないよう最低0.1でクリップ
    if entry <= 0 or tp <= 0 or sl <= 0:
        entry = max(entry, 0.1)
        tp = max(tp, 0.1)
        sl = max(sl, 0.1)

    return float(entry), float(tp), float(sl)