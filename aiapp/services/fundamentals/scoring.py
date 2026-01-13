# aiapp/services/fundamentals/scoring.py
# -*- coding: utf-8 -*-
"""
財務ファンダの簡易スコアリング（0..100）と flags 生成。

ねらい:
- “財務が強い” を雑にでも一貫して数値化 → picks_build_hybrid の加点要素にする
- 取得元が未確定でも input_fund.json さえあれば運用できる

見ている指標（metrics内のキー）:
- roe: ROE(%)
- op_margin: 営業利益率(%)
- sales_yoy: 売上YoY(%)
- equity_ratio: 自己資本比率(%)
- per: PER
- debt_ratio: 負債比率(%)（あれば）

返り値:
  (fund_score: float, flags: list[str])
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _f(x) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def score_fundamentals(metrics: Dict[str, Any]) -> Tuple[float, List[str]]:
    m = dict(metrics or {})
    roe = _f(m.get("roe"))
    opm = _f(m.get("op_margin"))
    yoy = _f(m.get("sales_yoy"))
    eqr = _f(m.get("equity_ratio"))
    per = _f(m.get("per"))
    debt = _f(m.get("debt_ratio"))  # 任意

    # ---- 部品スコア（0..1） ----
    # ROE: 0..20% を主戦場
    s_roe = _clamp(roe / 20.0, 0.0, 1.0)

    # 営業利益率: 0..15% を主戦場
    s_opm = _clamp(opm / 15.0, 0.0, 1.0)

    # 売上YoY: -10..+20% を主戦場（-10で0, +20で1）
    s_yoy = _clamp((yoy + 10.0) / 30.0, 0.0, 1.0)

    # 自己資本比率: 0..60% を主戦場
    s_eqr = _clamp(eqr / 60.0, 0.0, 1.0)

    # PER: 低い方が加点。ただし極端に低い(異常値)は抑える
    # 目安: PER 10で強い(1.0), 20で中(0.5), 40で弱(0.0)
    if per <= 0:
        s_per = 0.4  # unknown扱い
    else:
        s_per = _clamp(1.0 - (per - 10.0) / 30.0, 0.0, 1.0)

    # 負債比率（あれば）: 0..200%（低いほど良い）
    if debt <= 0:
        s_debt = 0.5
    else:
        s_debt = _clamp(1.0 - debt / 200.0, 0.0, 1.0)

    # ---- 重み（まずは固定） ----
    w_roe = 0.26
    w_opm = 0.22
    w_yoy = 0.18
    w_eqr = 0.16
    w_per = 0.12
    w_debt = 0.06

    score01 = (
        w_roe * s_roe
        + w_opm * s_opm
        + w_yoy * s_yoy
        + w_eqr * s_eqr
        + w_per * s_per
        + w_debt * s_debt
    )

    fund_score = float(_clamp(score01, 0.0, 1.0) * 100.0)

    # ---- flags ----
    flags: List[str] = []

    if roe >= 12:
        flags.append("ROE高め")
    elif roe <= 5 and roe != 0:
        flags.append("ROE弱め")

    if opm >= 10:
        flags.append("利益率強い")
    elif opm <= 4 and opm != 0:
        flags.append("利益率弱い")

    if yoy >= 8:
        flags.append("売上成長強い")
    elif yoy <= -2 and yoy != 0:
        flags.append("売上減速")

    if eqr >= 45:
        flags.append("財務健全")
    elif eqr <= 20 and eqr != 0:
        flags.append("財務注意")

    if per > 0:
        if per <= 12:
            flags.append("割安寄り")
        elif per >= 25:
            flags.append("割高寄り")

    if debt > 0:
        if debt >= 150:
            flags.append("負債重め")
        elif debt <= 60:
            flags.append("負債軽め")

    return fund_score, flags