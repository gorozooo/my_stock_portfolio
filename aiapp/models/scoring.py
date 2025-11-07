# -*- coding: utf-8 -*-
"""
AI銘柄スコアリング（短期/中期/長期 × 攻め/普通/守り）
- 入力: make_features() 済みの DataFrame（index=日付、終端行が最新）
- 出力: float スコア（大きいほど強い）

設計方針
- NaN/外れ値に強く（標準化の前に安全化）
- モード別の重み/ペナルティ
- スコア下限やルール本数などは settings から可変（デフォルトを内部に持つ）
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Literal, Dict

import numpy as np
import pandas as pd
from django.conf import settings


# ====== 設定（settings から上書き可能） ======
SCORE_FLOOR = float(getattr(settings, "AIAPP_SCORE_FLOOR", -5.0))
REQUIRE_RULES = int(getattr(settings, "AIAPP_REQUIRE_RULES", 1))  # 0=不問
STAR_MAX = 5

# horizon: 短期/中期/長期の内部窓
HORIZON_WINDOWS = {
    "short": {"look": 10, "ret": 5, "slope": 5},
    "mid":   {"look": 20, "ret": 10, "slope": 10},
    "long":  {"look": 60, "ret": 20, "slope": 20},
}

Mode = Literal["aggressive", "normal", "defensive"]
Horizon = Literal["short", "mid", "long"]


@dataclass(frozen=True)
class ScoreDetail:
    score: float
    stars: int
    reasons: Dict[str, float]
    rules_hit: int


# ====== 小物ユーティリティ ======

def _last(s: pd.Series) -> float:
    try:
        return float(s.iloc[-1])
    except Exception:
        return float("nan")

def _safe(v: float, default: float = 0.0) -> float:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return default
    return float(v)

def _z(v: float, mu: float, sd: float, clamp: float = 3.0) -> float:
    if sd <= 1e-12:
        return 0.0
    z = (v - mu) / sd
    return max(min(z, clamp), -clamp)

def _stars(score: float) -> int:
    # だいたい -5〜+5 を 1〜5⭐️に線形マップ
    s = (score + 5.0) / 10.0  # 0..1
    return int(np.clip(round(s * STAR_MAX), 1, STAR_MAX))

def _count_rules(feat: pd.DataFrame) -> int:
    """簡易ルール本数（例：GC/RSI帯/BBバンド/傾き）。"""
    ok = 0
    if _safe(_last(feat.get("GCROSS", pd.Series())), 0) == 1:
        ok += 1
    rsi = _safe(_last(feat.filter(like="RSI").iloc[:, -1]) if not feat.filter(like="RSI").empty else float("nan"))
    if 50 <= rsi <= 75:
        ok += 1
    bbz = _safe(_last(feat.get("BB_Z", pd.Series())), 0)
    if bbz > -0.5:  # 極端な下方乖離を回避
        ok += 1
    slope = _safe(_last(feat.filter(like="SLOPE_").iloc[:, 0]) if not feat.filter(like="SLOPE_").empty else float("nan"))
    if slope > 0:
        ok += 1
    return ok


# ====== コア採点 ======

def score_sample(
    feat: pd.DataFrame,
    mode: Mode = "aggressive",
    horizon: Horizon = "short",
) -> float:
    """
    互換API: 1銘柄ぶんの特徴量から総合スコアを返す（大きいほど上位）。
    """
    if feat is None or feat.empty:
        return -999.0

    # 窓
    h = HORIZON_WINDOWS.get(horizon, HORIZON_WINDOWS["short"])
    # 使う列の最新値
    close = feat["Close"].astype("float64")
    ret1  = _safe(_last(feat.get("RET_1", pd.Series(np.nan))), 0)
    ret5  = _safe(_last(feat.get("RET_5", pd.Series(np.nan))), 0)
    ret20 = _safe(_last(feat.get("RET_20", pd.Series(np.nan))), 0)
    vgap  = _safe(_last(feat.get("VWAP_GAP_PCT", pd.Series(np.nan))), 0)
    rsi   = _safe(_last(feat.filter(like="RSI").iloc[:, -1]) if not feat.filter(like="RSI").empty else float("nan"), 50)
    macd  = _safe(_last(feat.get("MACD_HIST", pd.Series(np.nan))), 0)
    atr   = _safe(_last(feat.filter(like="ATR").iloc[:, -1]) if not feat.filter(like="ATR").empty else float("nan"), 0)
    slope_s = _safe(_last(feat.filter(like="SLOPE_").iloc[:, 0]) if not feat.filter(like="SLOPE_").empty else float("nan"), 0)

    # 標準化のための簡易統計（直近 look 窓）
    recent = feat.iloc[-h["look"] :].copy()
    macd_mu, macd_sd = float(recent["MACD_HIST"].mean()), float(recent["MACD_HIST"].std(ddof=0) + 1e-12)
    rsi_mu, rsi_sd   = 50.0, 15.0  # RSIは固定基準でOK
    vgap_mu, vgap_sd = float(recent["VWAP_GAP_PCT"].mean()), float(recent["VWAP_GAP_PCT"].std(ddof=0) + 1e-12)
    ret5_mu, ret5_sd = float(recent["RET_5"].mean()), float(recent["RET_5"].std(ddof=0) + 1e-12)

    # Zに変換
    z_macd = _z(macd, macd_mu, macd_sd)
    z_rsi  = _z(rsi, rsi_mu + (5 if mode == "aggressive" else 0), rsi_sd)
    z_vgap = _z(vgap, vgap_mu, vgap_sd) * (-1.0)  # 乖離大きすぎは減点
    z_ret5 = _z(ret5, ret5_mu, ret5_sd)
    z_slope= slope_s  # すでに -1..+1 相当

    # モード別ウエイト
    if mode == "aggressive":
        w = dict(macd=1.3, rsi=1.0, vgap=0.8, ret5=1.2, slope=1.5)
        penalty_vol = 0.2   # ATRペナルティを弱め
    elif mode == "defensive":
        w = dict(macd=0.8, rsi=1.2, vgap=1.2, ret5=0.8, slope=0.8)
        penalty_vol = 0.6
    else:  # normal
        w = dict(macd=1.0, rsi=1.0, vgap=1.0, ret5=1.0, slope=1.0)
        penalty_vol = 0.4

    # スコア合成
    score = (
        w["macd"]  * z_macd +
        w["rsi"]   * z_rsi  +
        w["vgap"]  * z_vgap +
        w["ret5"]  * z_ret5 +
        w["slope"] * z_slope
    )

    # 変動ペナルティ（ATRを価格で割って無次元化）
    price = _safe(_last(close), 0)
    vol_pen = 0.0
    if price > 0 and atr > 0:
        vol_pen = penalty_vol * float(np.clip(atr / price * 100.0, 0, 10))  # 上限10%相当
    score -= vol_pen

    # ルール最低本数
    hit = _count_rules(feat)
    if REQUIRE_RULES > 0 and hit < REQUIRE_RULES:
        score -= 1.0 * (REQUIRE_RULES - hit)  # 足りない本数ぶん減点

    # 下限を適用（一覧を必ず10件埋めたいときは settings で極端に下げる）
    score = max(score, SCORE_FLOOR)
    return float(score)


# ====== 上位N件に使うヘルパ ======

def score_and_detail(
    feat: pd.DataFrame,
    mode: Mode = "aggressive",
    horizon: Horizon = "short",
) -> ScoreDetail:
    s = score_sample(feat, mode=mode, horizon=horizon)
    reasons = {
        "rsi": _safe(_last(feat.filter(like="RSI").iloc[:, -1]) if not feat.filter(like="RSI").empty else float("nan")),
        "macd_hist": _safe(_last(feat.get("MACD_HIST", pd.Series(np.nan)))),
        "vwap_gap_pct": _safe(_last(feat.get("VWAP_GAP_PCT", pd.Series(np.nan)))),
        "ret5": _safe(_last(feat.get("RET_5", pd.Series(np.nan)))),
        "slope": _safe(_last(feat.filter(like="SLOPE_").iloc[:, 0]) if not feat.filter(like="SLOPE_").empty else float("nan")),
    }
    return ScoreDetail(score=s, stars=_stars(s), reasons=reasons, rules_hit=_count_rules(feat))


__all__ = [
    "score_sample",
    "score_and_detail",
    "ScoreDetail",
]