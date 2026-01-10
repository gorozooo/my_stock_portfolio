# aiapp/services/picks_build/confirm_service.py
# -*- coding: utf-8 -*-
"""
“確実性（confirm）” を作るサービス。

目的:
- EV_true（期待値）を主キーにしたまま、エントリーの「形が良い」銘柄を上に寄せる補助スコアを作る。
- 例: ゴールデンクロス、直近高値更新、安値切り上げ、押し目反発など。

出力:
- confirm_score: 0..100（大きいほど追い風）
- confirm_flags: 何が効いたかのタグ（UI/デバッグ用）

注意:
- 欠損や列名揺れがあっても落ちない。
- “買い目線” の追い風を主に評価（売り目線は今は入れない）。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if not np.isfinite(v):
            return None
        return v
    except Exception:
        return None


def _last(df: pd.DataFrame, col: str) -> Optional[float]:
    try:
        if df is None or len(df) == 0 or col not in df.columns:
            return None
        return _f(df[col].iloc[-1])
    except Exception:
        return None


def _prev(df: pd.DataFrame, col: str) -> Optional[float]:
    try:
        if df is None or len(df) < 2 or col not in df.columns:
            return None
        return _f(df[col].iloc[-2])
    except Exception:
        return None


def _rolling_max_prev(df: pd.DataFrame, col: str, n: int) -> Optional[float]:
    """直近n本の“直前まで”の最大（最後の足は含めない）"""
    try:
        if df is None or len(df) < n + 1 or col not in df.columns:
            return None
        s = pd.to_numeric(df[col], errors="coerce")
        w = s.iloc[-(n + 1):-1]
        if len(w) == 0:
            return None
        v = float(np.nanmax(w.values))
        return v if np.isfinite(v) else None
    except Exception:
        return None


def _rolling_min_prev(df: pd.DataFrame, col: str, n: int) -> Optional[float]:
    """直近n本の“直前まで”の最小（最後の足は含めない）"""
    try:
        if df is None or len(df) < n + 1 or col not in df.columns:
            return None
        s = pd.to_numeric(df[col], errors="coerce")
        w = s.iloc[-(n + 1):-1]
        if len(w) == 0:
            return None
        v = float(np.nanmin(w.values))
        return v if np.isfinite(v) else None
    except Exception:
        return None


def compute_confirm(
    feat: pd.DataFrame,
    *,
    ma_short_col: str,
    ma_mid_col: str,
    rsi_col: str,
    last_close: Optional[float],
    atr: Optional[float],
) -> Tuple[int, List[str]]:
    """
    confirm_score（0..100）と flags を返す。
    """
    flags: List[str] = []
    score = 50  # ベース。追い風で加点、逆風で減点（最終0..100）

    if feat is None or len(feat) < 3:
        return 0, ["data_short"]

    last = _f(last_close)
    atrv = _f(atr)

    # =========================
    # 1) ゴールデンクロス（短期MAが中期MAを上抜け）
    # =========================
    ma_s = _last(feat, ma_short_col)
    ma_m = _last(feat, ma_mid_col)
    ma_s_prev = _prev(feat, ma_short_col)
    ma_m_prev = _prev(feat, ma_mid_col)

    if ma_s is not None and ma_m is not None and ma_s_prev is not None and ma_m_prev is not None:
        if ma_s_prev <= ma_m_prev and ma_s > ma_m:
            score += 18
            flags.append("golden_cross")
        elif ma_s < ma_m:
            score -= 6
            flags.append("below_mid_ma")

    # =========================
    # 2) 直近高値更新（20日高値更新）
    # =========================
    prev_high20 = _rolling_max_prev(feat, "High", 20) or _rolling_max_prev(feat, "HIGH", 20)
    if last is not None and prev_high20 is not None:
        if last >= prev_high20 * 1.000:
            score += 20
            flags.append("break_20d_high")

    # =========================
    # 3) 直近安値割れ（20日安値割れ） → 逆風
    # =========================
    prev_low20 = _rolling_min_prev(feat, "Low", 20) or _rolling_min_prev(feat, "LOW", 20)
    if last is not None and prev_low20 is not None:
        if last <= prev_low20 * 1.000:
            score -= 18
            flags.append("break_20d_low")

    # =========================
    # 4) 押し目反発（MA付近に戻って反発っぽい）
    #   条件例:
    #     - last が MA_mid の上
    #     - 直近5本の安値が 直近20本の安値に近い（“刺さって反発”）
    # =========================
    try:
        if last is not None and ma_m is not None and last > ma_m:
            prev_low5 = _rolling_min_prev(feat, "Low", 5) or _rolling_min_prev(feat, "LOW", 5)
            prev_low20_for_touch = prev_low20
            if prev_low5 is not None and prev_low20_for_touch is not None:
                # “底に触った” 判定（誤差は ATR があれば ATR、無ければ率）
                tol = (0.6 * atrv) if (atrv is not None and atrv > 0) else (0.015 * last)
                if abs(prev_low5 - prev_low20_for_touch) <= tol:
                    score += 12
                    flags.append("pullback_rebound")
    except Exception:
        pass

    # =========================
    # 5) RSI（過熱しすぎ/弱すぎの抑制）
    # =========================
    rsi = _last(feat, rsi_col)
    if rsi is not None:
        if rsi >= 72:
            score -= 8
            flags.append("rsi_overheat")
        elif rsi <= 32:
            score -= 6
            flags.append("rsi_weak")

    # =========================
    # 6) “安値切り上げ” の簡易（5日最安が20日最安より上）
    # =========================
    prev_low5 = _rolling_min_prev(feat, "Low", 5) or _rolling_min_prev(feat, "LOW", 5)
    if prev_low5 is not None and prev_low20 is not None:
        if prev_low5 > prev_low20 * 1.01:
            score += 10
            flags.append("higher_lows")

    # clamp
    score = int(max(0, min(100, score)))

    # flags が空なら最低限
    if not flags:
        flags = ["neutral"]

    return score, flags