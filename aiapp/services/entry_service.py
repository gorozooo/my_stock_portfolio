# -*- coding: utf-8 -*-
"""
entry_service.py
短期×攻め（暫定本番）の Entry / TP / SL を一貫ロジックで算出。

設計
- 入力: last, atr（いずれも float; NaN/None でも安全に処理）
- モード/時間軸を引数で受けるが、現状は "aggressive" × "short" を実装
- 高値掴み緩和のため、Entry は last + 0.05*ATR（従来の+0.10を弱める）
- TP/SL は ATR連動で固定比率（0.80 / 0.60）

必要に応じて tick 丸めを使う（デフォルトOFF）。
"""

from __future__ import annotations
from typing import Optional, Tuple

import math
import numpy as np

# --- オプション: 気配値刻みの丸め（簡易版/JPX全域を厳密には網羅しない） ---
def _round_tick(price: float, use_tick: bool = False) -> float:
    if not use_tick or not np.isfinite(price):
        return float(price)

    p = float(price)
    # 超簡易な刻み表（必要なら拡張）
    if p < 3000:    tick = 1
    elif p < 10000: tick = 5
    elif p < 50000: tick = 10
    else:           tick = 50
    return math.floor(p / tick + 1e-9) * tick

def compute_entry_tp_sl(
    last: Optional[float],
    atr: Optional[float],
    mode: str = "aggressive",
    horizon: str = "short",
    use_tick_round: bool = False,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    返り値: (entry, tp, sl) いずれも float or None
    """
    if last is None or atr is None:
        return None, None, None
    try:
        last = float(last)
        atr = float(atr)
    except Exception:
        return None, None, None

    if not np.isfinite(last) or not np.isfinite(atr) or atr <= 0:
        return None, None, None

    # 現仕様は短期×攻め固定（将来: mode/horizonで分岐）
    entry = last + 0.05 * atr
    tp    = entry + 0.80 * atr
    sl    = entry - 0.60 * atr

    if use_tick_round:
        entry = _round_tick(entry, True)
        tp    = _round_tick(tp, True)
        sl    = _round_tick(sl, True)

    return float(entry), float(tp), float(sl)