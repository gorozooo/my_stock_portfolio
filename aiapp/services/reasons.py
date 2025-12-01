"""
aiapp.services.reasons

特徴量から「選定理由 ×5」と「懸念（あれば）」を日本語で生成するモジュール。
トーン: 落ち着き＋前向き。専門用語は最小限にして、初心者でもイメージしやすい表現にする。

make_reasons(feat: dict) -> (reasons: list[str], concern: str | None)

feat には少なくとも以下の key が入っている想定（どれか欠けてもOK）:
  - ema_slope        : トレンドの傾き（SLOPE_20 など）
  - rel_strength_10  : 10日間の相対強度（ベンチマーク比％）
  - rsi14            : RSI14
  - vol_ma20_ratio   : 出来高 / 20日平均出来高
  - breakout_flag    : ブレイクしていれば 1
  - atr14            : ATR14
  - vwap_proximity   : VWAP からの乖離率（％）
  - last_price       : 終値（あれば、ATRの大きさ判断に利用）
"""

from __future__ import annotations
from typing import List, Tuple, Dict, Any, Optional


def _fmt_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}%"


def _fmt_x(v: float | None, digits: int = 1) -> str:
    """倍率用（1.8 → 1.8 倍）"""
    if v is None:
        return "—"
    return f"{v:.{digits}f}倍"


def _clamp(v: Optional[float], lo: float, hi: float) -> Optional[float]:
    if v is None:
        return None
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def make_reasons(feat: Dict[str, Any]) -> Tuple[List[str], str | None]:
    reasons: List[str] = []

    ema_slope: Optional[float] = feat.get("ema_slope")
    rel10: Optional[float] = feat.get("rel_strength_10")
    rsi: Optional[float] = feat.get("rsi14")
    vol_ratio: Optional[float] = feat.get("vol_ma20_ratio")
    breakout_flag: int = int(feat.get("breakout_flag", 0) or 0)
    atr: Optional[float] = feat.get("atr14")
    vwap_gap: Optional[float] = feat.get("vwap_proximity")
    last_price: Optional[float] = feat.get("last_price")

    # 1) トレンドの向き（EMA傾き）
    if ema_slope is None:
        reasons.append("今のトレンドははっきりしませんが、大きく傾いている状態ではありません。")
    else:
        if ema_slope > 0:
            # 強さで少し言い回しを変える
            if ema_slope > 0.8:
                reasons.append("短期と中期の平均線がそろって右肩上がりで、力強い上昇トレンドに乗りやすい形です。")
            elif ema_slope > 0.3:
                reasons.append("平均線がゆるやかに右肩上がりで、コツコツと上昇している流れに乗りやすい状態です。")
            else:
                reasons.append("平均線はわずかに上向きで、上昇に転じつつあるタイミングとみなしています。")
        else:
            if ema_slope < -0.5:
                reasons.append("平均線は下向きですが、戻り売りのポイントとして候補に入っています。")
            else:
                reasons.append("トレンドは横ばい〜やや弱めですが、下げ止まりを確認しつつの候補としています。")

    # 2) 相対強度（指数との比較）
    if rel10 is None:
        reasons.append("指数との細かい比較はできていませんが、単体の動きとしてはバランスを見ています。")
    else:
        r = rel10
        if r > 3:
            reasons.append(
                f"直近10日間は、日経平均などの指数よりもしっかり強い動き（約 {_fmt_pct(r)} 上回り）になっています。"
            )
        elif r > 0.5:
            reasons.append(
                f"直近10日間は、市場平均よりもやや強い動き（約 {_fmt_pct(r)} 上回り）が続いています。"
            )
        elif r > -0.5:
            reasons.append(
                "直近10日間は、市場平均とほぼ同じペースで動いており、極端に置いていかれてはいません。"
            )
        elif r > -3:
            reasons.append(
                f"直近10日間は少し出遅れ気味（約 {_fmt_pct(r)} 下回り）ですが、巻き返し狙いの候補としています。"
            )
        else:
            reasons.append(
                f"直近10日間は市場平均よりもかなり弱い動き（約 {_fmt_pct(r)} 下回り）ですが、反発余地に注目した候補です。"
            )

    # 3) RSI（買われすぎ/売られすぎ）
    if rsi is None:
        reasons.append("売られすぎ・買われすぎを示すRSIはデータ不足のため、中立扱いとしています。")
    else:
        val = _clamp(rsi, 0, 100) or rsi
        if val >= 70:
            reasons.append(f"短期的にはかなり買われやすい水準（RSI14={val:.0f}）で、勢いが強い一方で伸び切りにも注意が必要です。")
        elif val >= 60:
            reasons.append(f"やや買いが優勢な水準（RSI14={val:.0f}）で、素直な上昇トレンドに乗りやすい状態です。")
        elif val >= 40:
            reasons.append(f"買いと売りのバランスが良い、落ち着いた水準（RSI14={val:.0f}）です。")
        elif val >= 30:
            reasons.append(f"やや売られ気味の水準（RSI14={val:.0f}）で、リバウンド狙いの候補としています。")
        else:
            reasons.append(f"かなり売られすぎゾーン（RSI14={val:.0f}）で、反発が入れば戻り幅が狙いやすいと見ています。")

    # 4) 出来高（資金の集まり具合）
    if vol_ratio is None or vol_ratio <= 0:
        reasons.append("出来高は平均並みとみなし、過度な過熱感や閑散さは今のところ出ていません。")
    else:
        vr = vol_ratio
        if vr >= 2.0:
            reasons.append(f"出来高がここ最近の平均の {_fmt_x(vr)} と多く、資金が本格的に集まりつつある状況です。")
        elif vr >= 1.2:
            reasons.append(f"出来高が平均より少し多く、静かに買いが入ってきている形です（約 {_fmt_x(vr)}）。")
        elif vr >= 0.8:
            reasons.append("出来高はほぼ平均的で、落ち着いた流れの中でトレンドを追いやすい状態です。")
        else:
            reasons.append("出来高はやや少なめで、無理な急騰ではなく落ち着いた値動きが続いています。")

    # 5) ブレイク or レンジ
    if breakout_flag == 1:
        reasons.append(
            "直近の高値ゾーンを終値でしっかり上抜けていて、「上に走り始めた後半」よりも「走り出しの段階」を狙う設計です。"
        )
    else:
        if vwap_gap is not None and abs(vwap_gap) <= 1.0:
            reasons.append(
                "直近の取引の中心価格（VWAP）付近で落ち着いて推移しており、無理に高値を追わずにエントリーしやすい位置です。"
            )
        else:
            reasons.append(
                "はっきりした高値ブレイクは出ていませんが、直近の価格帯の中でじわじわと方向を出しつつある状態です。"
            )

    # ---------------------------
    # 懸念（あれば 1行）
    # ---------------------------
    concerns: List[str] = []

    # ATR の大きさ（値動きの荒さ）
    if atr is not None:
        # last_price があれば「何％くらい動くか」のイメージにする
        if last_price and last_price > 0:
            atr_pct = (atr / last_price) * 100.0
            if atr_pct >= 5.0:
                concerns.append(f"1日の値動きの幅がやや大きめ（目安で株価の約 {_fmt_pct(atr_pct)} 程度）で、ロットを持ちすぎるとブレに振り回されやすい点には注意が必要です。")
            elif atr_pct >= 3.0:
                concerns.append(f"値動きの幅がやや広め（株価の約 {_fmt_pct(atr_pct)} 程度）なので、損切りラインは少し余裕を持っておく必要があります。")
        else:
            if atr >= 10:
                concerns.append("値動きの幅（ATR）が比較的大きく、短期的な上下に振られやすい銘柄です。ロット管理に注意が必要です。")

    # 相対強度がかなり弱い
    if rel10 is not None and rel10 <= -5.0:
        concerns.append(f"直近10日間は市場平均よりもかなり弱い動き（約 {_fmt_pct(rel10)} 下回り）が続いており、反発まで時間がかかる可能性があります。")

    # RSI が高すぎ / 低すぎ
    if rsi is not None:
        if rsi >= 75:
            concerns.append("短期的にはかなり買われすぎのゾーンに入っており、いつ調整が入ってもおかしくない点には注意が必要です。")
        elif rsi <= 25:
            concerns.append("売られすぎの状態が長く続いており、さらに下を試す動きになった場合の割り切りも意識する必要があります。")

    # VWAP からの乖離が大きい
    if vwap_gap is not None and abs(vwap_gap) >= 3.0:
        if vwap_gap > 0:
            concerns.append(f"現在値が直近の取引の中心価格（VWAP）よりもやや上に離れており、短期的に伸び切りからの押し目になる可能性があります（乖離 {_fmt_pct(vwap_gap)} 前後）。")
        else:
            concerns.append(f"現在値がVWAPよりも下側に離れていて、戻り待ちの動きになる可能性があります（乖離 {_fmt_pct(vwap_gap)} 前後）。")

    # 1つにまとめる
    concern_text: Optional[str] = None
    if concerns:
        # 一番重要そうなものを 1つだけ採用（長くなりすぎないように）
        concern_text = concerns[0]

    return reasons[:5], concern_text