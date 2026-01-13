# aiapp/services/fundamentals/scoring.py
# -*- coding: utf-8 -*-
"""
財務ファンダのスコアリング（初期版）。

重要:
- ここは“雑でもいいから確実に回る”を優先
- 後で指標を増やしても、出力は fund_score + flags + metrics を守ればOK
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def score_fundamentals(metrics: Dict[str, Any]) -> Tuple[float, List[str]]:
    """
    metrics から 0..100 の fund_score と flags を作る。

    初期版:
    - 取れるものだけでスコア（無い項目は中立）
    - 強い効果は “地雷回避（減点）” 側に寄せる
    """
    score = 50.0
    flags: List[str] = []

    # 例: ROE
    roe = _to_float(metrics.get("roe"))
    if roe is not None:
        if roe >= 12:
            score += 10
            flags.append("高ROE")
        elif roe <= 4:
            score -= 10
            flags.append("低ROE")

    # 例: 営業利益率
    opm = _to_float(metrics.get("op_margin"))
    if opm is not None:
        if opm >= 10:
            score += 8
            flags.append("利益率良い")
        elif opm <= 3:
            score -= 8
            flags.append("利益率弱い")

    # 例: 売上成長率
    sales_g = _to_float(metrics.get("sales_yoy"))
    if sales_g is not None:
        if sales_g >= 8:
            score += 8
            flags.append("増収")
        elif sales_g <= -5:
            score -= 8
            flags.append("減収")

    # 例: 自己資本比率
    equity = _to_float(metrics.get("equity_ratio"))
    if equity is not None:
        if equity >= 40:
            score += 6
            flags.append("財務健全")
        elif equity <= 20:
            score -= 10
            flags.append("財務弱い")

    # 例: PER（高すぎ注意）
    per = _to_float(metrics.get("per"))
    if per is not None:
        if per <= 12:
            score += 5
            flags.append("割安寄り")
        elif per >= 30:
            score -= 7
            flags.append("割高注意")

    score = max(0.0, min(100.0, score))
    flags = flags[:10]
    return score, flags


def _to_float(x):
    try:
        if x is None:
            return None
        f = float(x)
        if f != f:
            return None
        return f
    except Exception:
        return None