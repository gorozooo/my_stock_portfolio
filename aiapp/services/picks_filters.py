# -*- coding: utf-8 -*-
"""
aiapp.services.picks_filters

・仕手株っぽい暴騰
・極端に薄い銘柄（売買代金が少なすぎ）
・ボラティリティが荒すぎる銘柄

などを「ピック生成の前段」でふるい落とすレイヤー。
数値はあくまで暫定値で、将来的に環境変数 or 設定ファイルから
切り替えられる前提で実装している。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np

Number = float


@dataclass
class FilterContext:
    code: str
    feat: Dict[str, Any]        # features.make_features() の最終行 dict
    last: Optional[Number]      # Close の終値
    atr: Optional[Number]       # ATR14 など


@dataclass
class FilterDecision:
    skip: bool
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None


# ====== 閾値（暫定 / 環境変数で微調整可能） ======

def _env_float(key: str, default: float) -> float:
    v = os.getenv(key)
    if v is None:
        return float(default)
    try:
        return float(v)
    except Exception:
        return float(default)


# 1日の売買代金が 3 億円未満は「薄い」とみなして除外候補
MIN_TURNOVER_YEN: float = _env_float("AIAPP_MIN_TURNOVER_YEN", 3e8)

# ATR が株価に対して 12% を超える銘柄は、短期トレードにはやや荒すぎるとみなす
MAX_ATR_PCT: float = _env_float("AIAPP_MAX_ATR_PCT", 12.0)

# 直近のリターンがあまりにも大きいものは「仕手ムーブ」の可能性として除外候補
PUMP_RET5: float = _env_float("AIAPP_PUMP_RET5", 0.25)   # 5日で +25% 以上
PUMP_RET20: float = _env_float("AIAPP_PUMP_RET20", 0.60)  # 20日で +60% 以上


def _get_float(d: Dict[str, Any], key: str) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    if not np.isfinite(f):
        return None
    return f


def _calc_turnover(ctx: FilterContext) -> Optional[float]:
    """
    直近1日の「売買代金 ≒ 終値 × 出来高」をざっくり計算。
    """
    last = ctx.last if ctx.last is not None and np.isfinite(ctx.last) else _get_float(ctx.feat, "Close")
    vol = _get_float(ctx.feat, "Volume")
    if last is None or vol is None or last <= 0 or vol <= 0:
        return None
    return float(last * vol)


def _check_liquidity(ctx: FilterContext) -> Optional[FilterDecision]:
    turnover = _calc_turnover(ctx)
    if turnover is None:
        # 情報が無いときはスルー（別条件でふるいにかける）
        return None
    if turnover < MIN_TURNOVER_YEN:
        return FilterDecision(
            skip=True,
            reason_code="LOW_TURNOVER",
            reason_text="直近の売買代金がかなり少なく、板が薄い可能性が高いため、短期トレード向きではないと判断しました。",
        )
    return None


def _check_volatility(ctx: FilterContext) -> Optional[FilterDecision]:
    atr = ctx.atr if ctx.atr is not None else _get_float(ctx.feat, "ATR14")
    last = ctx.last if ctx.last is not None else _get_float(ctx.feat, "Close")
    if atr is None or last is None or last <= 0:
        return None
    atr_pct = (float(atr) / float(last)) * 100.0
    if atr_pct >= MAX_ATR_PCT:
        return FilterDecision(
            skip=True,
            reason_code="TOO_VOLATILE",
            reason_text="1日の値動き幅がかなり大きく、ロットを持ちすぎるとブレに振り回されやすいと判断して、候補から外しています。",
        )
    return None


def _check_pump(ctx: FilterContext) -> Optional[FilterDecision]:
    """
    直近5日・20日リターンが極端に高いものを「仕手ムーブ」寄りとして除外。
    RET_* は make_features 側で 0.10=+10% といったスケールを想定。
    """
    r5 = _get_float(ctx.feat, "RET_5")
    r20 = _get_float(ctx.feat, "RET_20")
    if (r5 is not None and r5 >= PUMP_RET5) or (r20 is not None and r20 >= PUMP_RET20):
        return FilterDecision(
            skip=True,
            reason_code="PUMP_STYLE",
            reason_text="直近の上昇率がかなり急で、短期的な仕手・イベント要因の影響が大きい可能性があるため、今回は見送りとしています。",
        )
    return None


def check_all(ctx: FilterContext) -> FilterDecision:
    """
    すべてのフィルタを順番に適用し、どれか1つでも hit したらその時点で戻る。
    何も引っかからなければ skip=False を返す。
    """
    for fn in (_check_liquidity, _check_volatility, _check_pump):
        dec = fn(ctx)
        if dec is not None:
            return dec
    return FilterDecision(skip=False)