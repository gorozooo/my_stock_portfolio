"""
aiapp.services.reasons
特徴量とスコアから、理由×5 と 懸念（任意）を日本語で生成。
トーン: 落ち着き＋前向き（やや感情をのせる）

- make_reasons(feat: dict) -> (reasons: list[str], concern: str|None)
"""

from __future__ import annotations
from typing import List, Tuple

def _fmt_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}%"

def make_reasons(feat: dict) -> Tuple[List[str], str | None]:
    reasons: List[str] = []

    # 1) トレンド整合
    slope = feat.get("ema_slope")
    if slope is not None and slope > 0:
        reasons.append("週足×日足が揃って上向き（EMA傾きがプラス）")
    else:
        reasons.append("方向感は限定的（EMA傾きは強くない）")

    # 2) 相対強度
    rs = feat.get("rel_strength_10")
    if rs is not None:
        if rs > 0:
            reasons.append(f"ベンチマーク比で強さを維持（10日差 {_fmt_pct(rs)}）")
        else:
            reasons.append(f"直近はベンチ比でやや弱い（10日差 {_fmt_pct(rs)}）")
    else:
        reasons.append("相対強度は参考値（ベンチ取得なし）")

    # 3) モメンタム
    rsi = feat.get("rsi14")
    if rsi is not None:
        if rsi >= 55:
            reasons.append(f"モメンタムが前向き（RSI14={rsi:.0f}）")
        elif rsi <= 45:
            reasons.append(f"短期はやや疲れ（RSI14={rsi:.0f}）")
        else:
            reasons.append(f"RSI14は中立（{rsi:.0f}）")
    else:
        reasons.append("RSIは判定保留（データ不足）")

    # 4) 出来高
    volr = feat.get("vol_ma20_ratio")
    if volr is not None:
        if volr >= 1.5:
            reasons.append("出来高が平均比で増加（資金流入の兆し）")
        elif volr <= 0.8:
            reasons.append("出来高が平均比で細り（様子見になりやすい）")
        else:
            reasons.append("出来高は平均的（過不足なし）")
    else:
        reasons.append("出来高傾向は参考値")

    # 5) 節目/ブレイク
    if int(feat.get("breakout_flag", 0)) == 1:
        reasons.append("直近レジスタンスを終値で突破（ブレイク）")
    else:
        reasons.append("明確なブレイクは未確認")

    # 懸念
    concern = None
    atr = feat.get("atr14")
    if atr is not None and atr > 12:  # 短期×攻めのざっくり基準
        concern = "ボラティリティが高め。サイズは控えめ推奨"

    prox = feat.get("vwap_proximity")
    if prox is not None and prox > 3.0:
        concern = (concern + "／" if concern else "") + "価格がVWAPから離れ気味（伸び切り警戒）"

    return reasons[:5], concern
