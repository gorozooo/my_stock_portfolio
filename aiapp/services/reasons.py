"""
aiapp.services.reasons
特徴量とスコアから、理由×5 と 懸念（任意）を日本語で生成。
トーン: 落ち着き＋前向き（やさしく・初心者向け）

- make_reasons(feat: dict) -> (reasons: list[str], concern: str|None)
"""

from __future__ import annotations
from typing import List, Tuple


def _fmt_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}%"


def make_reasons(feat: dict) -> Tuple[List[str], str | None]:
    """
    feat から「この銘柄を候補に選んだ主なポイント」を 5つ返す。
    できるだけ専門用語を減らし、初心者でもイメージしやすい言い方に寄せる。
    """
    reasons: List[str] = []

    # 1) トレンド（方向感）
    slope = feat.get("ema_slope")
    if slope is None:
        reasons.append("直近の流れはハッキリせず、横ばいに近い動きです。")
    else:
        if slope > 0:
            reasons.append("ここしばらくは右肩上がりの流れが続いていて、上昇トレンド寄りと判断しています。")
        elif slope < 0:
            reasons.append("足元ではやや下向きの流れですが、大きく崩れているわけではありません。")
        else:
            reasons.append("直近は上げ下げが交互に出ており、方向感はまだはっきりしていません。")

    # 2) 相対強度（市場全体との強さ比べ）
    rs = feat.get("rel_strength_10")
    if rs is not None:
        if rs > 0:
            reasons.append(
                f"最近10日間は、市場全体と比べてやや強い動きです（差 {_fmt_pct(rs)} 程度のプラス）。"
            )
        elif rs < 0:
            reasons.append(
                f"最近10日間は、市場全体よりややもたついています（差 {_fmt_pct(rs)} 程度のマイナス）。"
            )
        else:
            reasons.append("最近10日間は、市場全体とほぼ同じような動きが続いています。")
    else:
        reasons.append("市場全体との強さ比較データが一部足りないため、相対的な強さは参考値としています。")

    # 3) モメンタム（勢い）
    rsi = feat.get("rsi14")
    if rsi is not None:
        if rsi >= 70:
            reasons.append(f"短期的にはかなり買いが偏っており、勢いは強めです（勢い指標 {rsi:.0f}）。")
        elif rsi >= 55:
            reasons.append(f"買いと売りのバランスはやや買い優勢で、上方向への勢いがあります（指標 {rsi:.0f}）。")
        elif rsi <= 30:
            reasons.append(f"かなり売られた後の水準で、下げ一巡からの戻りに注意したい場面です（指標 {rsi:.0f}）。")
        elif rsi <= 45:
            reasons.append(f"いったん売りが優勢だったあとで、落ち着きつつある水準です（指標 {rsi:.0f}）。")
        else:
            reasons.append(f"買われすぎ・売られすぎのどちらにも偏っておらず、ニュートラルな状態です（指標 {rsi:.0f}）。")
    else:
        reasons.append("短期の勢いを示す指標はデータ不足のため、今回はトレンドや出来高の情報を重視しています。")

    # 4) 出来高（売買の活発さ）
    volr = feat.get("vol_ma20_ratio")
    if volr is not None:
        if volr >= 1.5:
            reasons.append("最近の売買量が普段より多く、資金が集まりやすい状況になっています。")
        elif volr <= 0.8:
            reasons.append("売買量はやや少なめで、参加者は多くありません。急な値動きには注意が必要です。")
        else:
            reasons.append("売買量は平常時に近く、極端に薄いわけでも過熱しているわけでもありません。")
    else:
        reasons.append("売買量のデータが一部欠けているため、出来高については控えめに評価しています。")

    # 5) 節目／ブレイク
    if int(feat.get("breakout_flag", 0)) == 1:
        reasons.append("最近の高値ゾーンをしっかり上抜けていて、『ひと段落ついた上抜け』としてピックアップしています。")
    else:
        reasons.append("明確な高値ブレイクはまだ出ていませんが、いまの水準からの動き次第でチャンスになる可能性があります。")

    # ── 懸念ポイント（あれば1〜2個をまとめて1文に） ──
    concern_parts: List[str] = []

    atr = feat.get("atr14")
    if atr is not None and atr > 12:  # 短期×攻めのざっくり基準
        concern_parts.append("値動きの幅が大きめで、短い時間でも上下に振れやすい銘柄です。ロットは少なめ推奨です。")

    prox = feat.get("vwap_proximity")
    if prox is not None and prox > 3.0:
        concern_parts.append("直近の平均的な取引価格からやや離れた位置にあり、伸び切りからの反動には注意が必要です。")

    concern = "／".join(concern_parts) if concern_parts else None

    return reasons[:5], concern