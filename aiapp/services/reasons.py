# -*- coding: utf-8 -*-
"""
aiapp.services.reasons

特徴量から「選定理由 ×5」と「懸念（あれば）」を日本語で生成するモジュール。
トーン: 落ち着き＋前向き。専門用語は最小限にして、初心者でもイメージしやすい表現にする。

make_reasons(feat: dict) -> (reasons: list[str], concern: str | None)

feat には少なくとも以下の key が入っている想定（どれか欠けてもOK）:
  - ema_slope / SLOPE_25 / SLOPE_5        : トレンドの傾き
  - rel_strength_10                        : 10日間の相対強度（％換算）
  - rsi14 / RSI14                          : RSI14
  - vol_ma_ratio                           : 出来高 / 25日平均出来高（無ければ Volume＋MA25 から算出）
  - breakout_flag                          : ブレイクしていれば 1
  - atr14 / ATR14                          : ATR14
  - vwap_proximity / VWAP_GAP_PCT          : VWAP からの乖離率（％）
  - last_price / Close                     : 終値（ATRの大きさ判断に利用）

※ features.py 側の主な列
  MA5, MA25, MA75, MA100, MA200
  RSI14
  ATR14
  VWAP, VWAP_GAP_PCT
  RET_1, RET_5, RET_20
  SLOPE_5, SLOPE_25
  GCROSS, DCROSS
"""

from __future__ import annotations

import math
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


def _as_float(x: Any) -> Optional[float]:
    """NaN や変換失敗は None に揃える"""
    try:
        if x is None:
            return None
        v = float(x)
        if math.isnan(v):
            return None
        return v
    except Exception:
        return None


def make_reasons(feat: Dict[str, Any]) -> Tuple[List[str], str | None]:
    """
    「この銘柄を候補に入れている理由」を最大5行、
    「気をつけたいポイント」を1行だけ返す。
    データが無い指標については、無理にコメントを入れない。
    """
    reasons: List[str] = []

    # ---------------------------
    # 入力マッピング（features の列名 → 内部変数）
    # ---------------------------

    # トレンド傾き: ema_slope → SLOPE_25 → SLOPE_5
    ema_slope: Optional[float] = _as_float(
        feat.get("ema_slope")
        or feat.get("SLOPE_25")
        or feat.get("SLOPE_5")
    )

    # 相対強度（10日間の指数比。既に％換算されている想定）
    rel10: Optional[float] = _as_float(
        feat.get("rel_strength_10")
    )

    # RSI（features.py では RSI14）
    rsi: Optional[float] = _as_float(
        feat.get("rsi14")
        or feat.get("RSI14")
    )

    # 出来高倍率（Volume / MA25 前提）
    vol_ratio: Optional[float] = _as_float(
        feat.get("vol_ma_ratio")
    )

    breakout_flag_raw = feat.get("breakout_flag", 0) or 0
    try:
        breakout_flag: int = int(breakout_flag_raw)
    except Exception:
        breakout_flag = 0

    # ATR（features.py は ATR14）
    atr: Optional[float] = _as_float(
        feat.get("atr14")
        or feat.get("ATR14")
    )

    # VWAP 乖離（％）
    vwap_gap: Optional[float] = _as_float(
        feat.get("vwap_proximity")
        or feat.get("VWAP_GAP_PCT")
    )

    # 終値
    last_price: Optional[float] = _as_float(
        feat.get("last_price")
        or feat.get("Close")
    )

    # vol_ratio が dict に無い場合は Volume / MA25 から計算
    if vol_ratio is None:
        vol = _as_float(feat.get("Volume"))
        ma25 = _as_float(feat.get("MA25"))
        if vol is not None and ma25 is not None and ma25 > 0:
            vol_ratio = vol / ma25

    # ---------------------------
    # 1) トレンドの向き（MA傾き）
    # ---------------------------
    if ema_slope is not None:
        if ema_slope > 0.8:
            reasons.append(
                "短期と中期の平均線がそろって力強い右肩上がりで、はっきりした上昇トレンドに乗りやすい形です。"
            )
        elif ema_slope > 0.3:
            reasons.append(
                "平均線が素直な右肩上がりで、押し目を拾いながらトレンドに沿ったエントリーを狙いやすい状態です。"
            )
        elif ema_slope > 0.05:
            reasons.append(
                "平均線が徐々に上向きに変化しており、本格的な上昇トレンドへの立ち上がりを拾いにいく形です。"
            )
        elif ema_slope > -0.1:
            reasons.append(
                "大きな方向感は出ていませんが、下げ止まりからの持ち直しを狙える位置と判断しています。"
            )
        else:
            reasons.append(
                "中期ではまだ下向きのトレンドですが、直近で下げ止まりの兆しが出ているため、反発候補としてピックアップしています。"
            )

    # ---------------------------
    # 2) 相対強度（指数との比較）
    # ---------------------------
    if rel10 is not None:
        r = rel10
        if r > 5:
            reasons.append(
                f"直近10日間で、日経平均などの指数を大きく上回る強さ（約 {_fmt_pct(r)} 上回り）が続いており、資金の集まりが明確な銘柄です。"
            )
        elif r > 2:
            reasons.append(
                f"直近10日間で、市場平均よりもしっかり強い動き（約 {_fmt_pct(r)} 上回り）になっていて、相対的な強さが際立っています。"
            )
        elif r > 0.5:
            reasons.append(
                f"直近10日間で、市場平均をわずかに上回るペース（約 {_fmt_pct(r)} 上回り）で推移しており、堅実な強さが続いています。"
            )
        elif r > -1.0:
            reasons.append(
                "直近10日間で、市場平均とほぼ同じペースで推移しており、極端な出遅れや先走りのないバランスの良い位置です。"
            )
        else:
            reasons.append(
                f"直近10日間ではやや出遅れ気味（約 {_fmt_pct(r)} 下回り）ですが、巻き返し狙いのリバウンド候補として位置付けています。"
            )

    # ---------------------------
    # 3) RSI（買われすぎ/売られすぎ）
    # ---------------------------
    if rsi is not None:
        val = _clamp(rsi, 0, 100) or rsi
        if val >= 75:
            reasons.append(
                f"RSI14が {val:.0f} とかなり強いゾーンにあり、短期的な勢いが乗っている局面です。"
            )
        elif val >= 60:
            reasons.append(
                f"RSI14が {val:.0f} と買いが優勢な水準で、素直な上昇トレンドに乗りやすい状態です。"
            )
        elif val >= 45:
            reasons.append(
                f"RSI14が {val:.0f} 付近と、中立〜やや強めのバランスで落ち着いており、無理なくエントリーしやすい水準です。"
            )
        elif val >= 30:
            reasons.append(
                f"RSI14が {val:.0f} とやや売られ気味のゾーンにあり、反発を狙いやすい位置と見ています。"
            )
        else:
            reasons.append(
                f"RSI14が {val:.0f} と極端な売られすぎゾーンにあり、反発が入った際の戻り幅に期待できる局面です。"
            )

    # ---------------------------
    # 4) 出来高（資金の集まり具合） Volume＋MA25 起点
    # ---------------------------
    if vol_ratio is not None and vol_ratio > 0:
        vr = vol_ratio
        if vr >= 3.0:
            reasons.append(
                f"出来高が最近の平均の {_fmt_x(vr)} と非常に多く、短期的に強い資金流入が確認できる銘柄です。"
            )
        elif vr >= 1.5:
            reasons.append(
                f"出来高が25日平均の {_fmt_x(vr)} 程度と増えてきており、静かに買いが集まりつつある状況です。"
            )
        elif vr >= 0.8:
            reasons.append(
                "出来高はおおむね平均並みで、過度な仕手化や極端な閑散感がなく、素直な値動きが期待しやすい環境です。"
            )
        else:
            reasons.append(
                "出来高はやや控えめですが、その分、急な乱高下が出にくい落ち着いた値動きになっています。"
            )

    # ---------------------------
    # 5) ブレイク or 位置取り（VWAPとの関係）
    # ---------------------------
    if breakout_flag == 1:
        reasons.append(
            "直近の高値ゾーンを明確に上抜けており、「上昇の走り出し」を狙うブレイクアウト型のエントリー候補です。"
        )
    else:
        if vwap_gap is not None:
            if abs(vwap_gap) <= 1.0:
                reasons.append(
                    "現在値が直近の取引の中心価格（VWAP）付近に位置しており、極端に高値掴みになりにくい落ち着いたエントリーポイントです。"
                )
            elif vwap_gap < -1.0:
                reasons.append(
                    f"現在値がVWAPよりやや下側（乖離 {_fmt_pct(vwap_gap)} 前後）に位置しており、押し目を拾いにいく形のエントリーが狙えます。"
                )
            else:
                reasons.append(
                    f"現在値がVWAPよりやや上側（乖離 {_fmt_pct(vwap_gap)} 前後）に位置しており、勢いに乗りつつもまだ伸びしろが期待できる水準です。"
                )

    # ---------------------------
    # 懸念（あれば 1行）
    # ---------------------------
    concerns: List[str] = []

    # ATR の大きさ（値動きの荒さ）
    if atr is not None:
        if last_price and last_price > 0:
            atr_pct = (atr / last_price) * 100.0
            if atr_pct >= 6.0:
                concerns.append(
                    f"1日の値動きの幅が比較的大きく（目安で株価の約 {_fmt_pct(atr_pct)} 程度）、ロットを持ちすぎるとブレに振り回されやすい点には注意が必要です。"
                )
            elif atr_pct >= 3.0:
                concerns.append(
                    f"値動きの幅がやや広め（株価の約 {_fmt_pct(atr_pct)} 程度）なので、損切りラインは少し余裕を持って置いておく必要があります。"
                )
        else:
            if atr >= 10:
                concerns.append(
                    "値動きの幅（ATR）が比較的大きく、短期的な上下に振られやすい銘柄です。ロット管理と損切り位置には注意が必要です。"
                )

    # 相対強度がかなり弱い
    if rel10 is not None and rel10 <= -5.0:
        concerns.append(
            f"直近10日間は市場平均よりもかなり弱い動き（約 {_fmt_pct(rel10)} 下回り）が続いており、反発まで時間がかかる可能性があります。"
        )

    # RSI が極端
    if rsi is not None:
        if rsi >= 80:
            concerns.append(
                "短期的にはかなり買われすぎのゾーンに入っており、いつ調整が入ってもおかしくない局面です。"
            )
        elif rsi <= 20:
            concerns.append(
                "売られすぎの状態が長く続いており、さらに下を試す展開になった場合の割り切りも意識する必要があります。"
            )

    # VWAP からの乖離が大きい
    if vwap_gap is not None and abs(vwap_gap) >= 5.0:
        if vwap_gap > 0:
            concerns.append(
                f"現在値が直近の取引の中心価格（VWAP）からやや上に離れており（乖離 {_fmt_pct(vwap_gap)} 前後）、短期的な伸び切りからの押し目に注意が必要です。"
            )
        else:
            concerns.append(
                f"現在値がVWAPよりも下側に大きく離れていて（乖離 {_fmt_pct(vwap_gap)} 前後）、戻り待ちの時間が長くなる可能性があります。"
            )

    # 1つにまとめる
    concern_text: Optional[str] = None
    if concerns:
        concern_text = concerns[0]

    return reasons[:5], concern_text