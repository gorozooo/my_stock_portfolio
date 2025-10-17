# portfolio/services/notify_tuning.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any

def decide_threshold(base: float, env: Dict[str, Any]) -> float:
    """
    base: policyの基本しきい値（例: 0.55）
    env:  {"breadth_score":-1..+1, "pf_rs":-1..+1}
    ルール:
      - 地合い弱い（<=-0.35）→ しきい値を-0.05（通知を出しやすく）
      - 地合い強い（>=+0.35）→ しきい値を+0.03（厳しめ）
      - PFが弱い（<=-0.25）→ -0.03、強い（>=+0.25）→ +0.02
    クリップ: 0.45..0.75
    """
    t = float(base)
    b = float(env.get("breadth_score", 0.0))
    r = float(env.get("pf_rs", 0.0))

    if b <= -0.35: t -= 0.05
    elif b >= 0.35: t += 0.03

    if r <= -0.25: t -= 0.03
    elif r >= 0.25: t += 0.02

    return max(0.45, min(0.75, t))