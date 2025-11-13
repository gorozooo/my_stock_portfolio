# -*- coding: utf-8 -*-
"""
AIエントリー計算サービス（Entry / TP / SL）
- モード(style) × 時間軸(horizon) で係数を管理するポリシー駆動設計
- 係数は RULES テーブルをいじるだけで変更可能
- picks_build からは compute_entry_tp_sl(last, atr, style=..., horizon=...) を呼び出す

計算ルール（共通）:
    entry = last + entry_k * ATR
    TP    = entry + tp_k    * ATR
    SL    = entry - sl_k    * ATR

注意:
    - last <= 0 または ATR <= 0 の場合は (None, None, None) を返す
    - 未知の style/horizon が来た場合は (DEFAULT_STYLE, DEFAULT_HORIZON) にフォールバック
"""

from __future__ import annotations
from typing import Dict, Tuple

DEFAULT_STYLE = "aggressive"
DEFAULT_HORIZON = "short"

# ------------------------------------------------------------
# ルールテーブル（あとからここだけいじれば挙動を変えられる）
# ------------------------------------------------------------
# entry_k:  エントリーをどれだけ現在値からずらすか（+側）
# tp_k:     TP を entry からどれだけ上に置くか
# sl_k:     SL を entry からどれだけ下に置くか
#
# すべて ATR 何本分か、という係数で管理する。
RULES: Dict[Tuple[str, str], Dict[str, float]] = {
    # 短期 × 攻め（今まで使っていた本命ルール）
    ("aggressive", "short"): {
        "entry_k": 0.05,   # ちょい上で待つ（高値掴み緩和）
        "tp_k":    0.80,   # 利確は広め
        "sl_k":    0.60,   # 損切りはややタイト
    },

    # 短期 × ふつう
    ("normal", "short"): {
        "entry_k": 0.02,
        "tp_k":    0.60,
        "sl_k":    0.50,
    },

    # 短期 × 守り
    ("defensive", "short"): {
        "entry_k": 0.00,
        "tp_k":    0.40,
        "sl_k":    0.40,
    },

    # 中期（仮パラメータ：あとでチューニング前提）
    ("aggressive", "mid"): {
        "entry_k": 0.05,
        "tp_k":    1.20,
        "sl_k":    0.80,
    },
    ("normal", "mid"): {
        "entry_k": 0.02,
        "tp_k":    0.90,
        "sl_k":    0.70,
    },
    ("defensive", "mid"): {
        "entry_k": 0.00,
        "tp_k":    0.70,
        "sl_k":    0.60,
    },

    # 長期（仮パラメータ）
    ("aggressive", "long"): {
        "entry_k": 0.05,
        "tp_k":    1.60,
        "sl_k":    1.00,
    },
    ("normal", "long"): {
        "entry_k": 0.02,
        "tp_k":    1.20,
        "sl_k":    0.80,
    },
    ("defensive", "long"): {
        "entry_k": 0.00,
        "tp_k":    1.00,
        "sl_k":    0.70,
    },
}


def _normalize(style: str | None, horizon: str | None) -> Tuple[str, str]:
    """style/horizon を小文字化＆未知値をデフォルトにフォールバック。"""
    s = (style or DEFAULT_STYLE).strip().lower()
    h = (horizon or DEFAULT_HORIZON).strip().lower()

    if (s, h) in RULES:
        return s, h

    # style だけ既知 / horizon が未知の場合なども、できるだけマシなペアに寄せる
    if (s, DEFAULT_HORIZON) in RULES:
        return s, DEFAULT_HORIZON
    if (DEFAULT_STYLE, h) in RULES:
        return DEFAULT_STYLE, h

    return DEFAULT_STYLE, DEFAULT_HORIZON


def compute_entry_tp_sl(
    last: float,
    atr: float,
    style: str = DEFAULT_STYLE,
    horizon: str = DEFAULT_HORIZON,
) -> Tuple[float | None, float | None, float | None]:
    """
    Entry / TP / SL を返すメイン関数。

    Parameters
    ----------
    last : float
        現在値（直近終値など）
    atr : float
        ATR（ボラティリティ指標）
    style : str
        "aggressive" / "normal" / "defensive" など
    horizon : str
        "short" / "mid" / "long" など

    Returns
    -------
    (entry, tp, sl) : Tuple[float | None, float | None, float | None]
    """
    try:
        last_f = float(last)
        atr_f = float(atr)
    except (TypeError, ValueError):
        return None, None, None

    if not (last_f > 0) or not (atr_f > 0):
        return None, None, None

    key = _normalize(style, horizon)
    rule = RULES.get(key) or RULES[(DEFAULT_STYLE, DEFAULT_HORIZON)]

    entry_k = float(rule.get("entry_k", 0.0))
    tp_k = float(rule.get("tp_k", 0.0))
    sl_k = float(rule.get("sl_k", 0.0))

    entry = last_f + entry_k * atr_f
    tp = entry + tp_k * atr_f
    sl = entry - sl_k * atr_f

    return float(entry), float(tp), float(sl)


__all__ = [
    "compute_entry_tp_sl",
    "RULES",
    "DEFAULT_STYLE",
    "DEFAULT_HORIZON",
]