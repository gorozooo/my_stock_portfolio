# aiapp/management/commands/picks_build.py
# -*- coding: utf-8 -*-
"""
AIピック生成コマンド（FULL + TopK + Sizing + 理由テキスト）

========================================
▼ 全体フロー（1銘柄あたり）
========================================
  1. 価格取得（OHLCV）
  2. 特徴量生成（テクニカル指標など）
  3. フィルタリング層（仕手株・流動性・異常値などで土台から除外）
  4. スコアリング / ⭐️算出
     ★本番仕様：⭐️は BehaviorStats（銘柄×mode_period×mode_aggr別）を参照（無ければハイブリッド or フォールバック）
  5. Entry / TP / SL の計算
  6. Sizing（数量・必要資金・想定PL/損失・見送り理由）
  7. 理由テキスト生成（選定理由×最大5行 + 懸念1行）
  8. バイアス層（セクター波 / 大型・小型バランスの微調整）
  9. ランキング（score_100 降順 → 株価降順）→ JSON 出力

========================================
▼ 利用サービス / モジュール
========================================
  ・価格取得:
      aiapp.services.fetch_price.get_prices

  ・特徴量生成:
      aiapp.models.features.make_features
    （OHLCV から MA, ボリンジャー, RSI, MACD, ATR, VWAP,
      RET_x, SLOPE_x, GCROSS/DCROSS などを計算）

  ・スコア:
      aiapp.services.scoring_service.score_sample

  ・⭐️（本番）:
      aiapp.models.behavior_stats.BehaviorStats
      （code × mode_period × mode_aggr の stars を参照）
    ★追加: 精度重視ハイブリッド
      - BehaviorStats がある場合は、(市場適合⭐️ × 実績適合⭐️) を信頼度でブレンド

  ・Entry / TP / SL:
      aiapp.services.entry_service.compute_entry_tp_sl
    ※ 無い場合は ATR ベースのフォールバックを使用。

  ・数量 / 必要資金 / 想定PL / 想定損失 / 見送り理由:
      aiapp.services.sizing_service.compute_position_sizing

  ・理由5つ + 懸念（日本語テキスト）:
      aiapp.services.reasons.make_reasons

  ・銘柄フィルタ層:
      aiapp.services.picks_filters.FilterContext
      aiapp.services.picks_filters.check_all

  ・セクター波 / 大型・小型バランス調整:
      aiapp.services.picks_bias.apply_all

========================================
▼ 出力ファイル
========================================
  - media/aiapp/picks/latest_full_all.json
  - media/aiapp/picks/latest_full.json
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from aiapp.services.fetch_price import get_prices
from aiapp.models.features import make_features, FeatureConfig
from aiapp.services.sizing_service import compute_position_sizing

# オプション扱いのサービス群（無くても動くように）
try:
    from aiapp.models import StockMaster
except Exception:  # pragma: no cover
    StockMaster = None  # type: ignore

try:
    from aiapp.services.reasons import make_reasons as make_ai_reasons
except Exception:  # pragma: no cover
    make_ai_reasons = None  # type: ignore

try:
    from aiapp.services.scoring_service import (
        score_sample as ext_score_sample,
        stars_from_score as ext_stars_from_score,  # 市場適合⭐️（フォールバック/ハイブリッド基礎）
    )
except Exception:  # pragma: no cover
    ext_score_sample = None  # type: ignore
    ext_stars_from_score = None  # type: ignore

try:
    from aiapp.services.entry_service import compute_entry_tp_sl as ext_entry_tp_sl
except Exception:  # pragma: no cover
    ext_entry_tp_sl = None  # type: ignore

# 追加: フィルタ層 & バイアス層
try:
    from aiapp.services.picks_filters import FilterContext, check_all as picks_check_all
except Exception:  # pragma: no cover
    FilterContext = None  # type: ignore
    picks_check_all = None  # type: ignore

try:
    from aiapp.services.picks_bias import apply_all as apply_bias_all
except Exception:  # pragma: no cover
    apply_all_bias = apply_bias_all  # typo防止
    apply_bias_all = apply_bias_all  # type: ignore
except NameError:
    apply_bias_all = None  # type: ignore

# 追加: マクロレジーム（あれば使う）
try:
    from aiapp.models.macro import MacroRegimeSnapshot
except Exception:  # pragma: no cover
    MacroRegimeSnapshot = None  # type: ignore

# ★追加: 行動統計（⭐️本番仕様）
try:
    from aiapp.models.behavior_stats import BehaviorStats
except Exception:  # pragma: no cover
    BehaviorStats = None  # type: ignore


# =========================================================
# 共通設定
# =========================================================

PICKS_DIR = Path("media/aiapp/picks")
PICKS_DIR.mkdir(parents=True, exist_ok=True)

JST = dt_timezone(timedelta(hours=9))


def dt_now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


BUILD_LOG = _env_bool("AIAPP_BUILD_LOG", False)


# =========================================================
# ヘルパ
# =========================================================

def _safe_series(x) -> pd.Series:
    """
    どんな形で来ても 1D pd.Series[float] に正規化する。
    """
    if x is None:
        return pd.Series(dtype="float64")
    if isinstance(x, pd.Series):
        return x.astype("float64")
    if isinstance(x, pd.DataFrame):
        if x.shape[1] == 0:
            return pd.Series(dtype="float64")
        return x.iloc[:, -1].astype("float64")
    try:
        arr = np.asarray(x, dtype="float64")
        if arr.ndim == 0:
            return pd.Series([float(arr)], dtype="float64")
        return pd.Series(arr, dtype="float64")
    except Exception:
        return pd.Series(dtype="float64")


def _series_tail_to_list(s, max_points: int = 60) -> Optional[List[Optional[float]]]:
    """
    pd.Series などから末尾 max_points 本だけ取り出して
    JSON 化しやすい Python の list[float | None] に変換する。
    NaN / inf は None にする。
    """
    ser = _safe_series(s)
    if ser.empty:
        return None
    ser = ser.tail(max_points)
    out: List[Optional[float]] = []
    for v in ser:
        try:
            f = float(v)
        except Exception:
            f = float("nan")
        if not np.isfinite(f):
            out.append(None)
        else:
            out.append(f)
    return out if out else None


def _safe_float(x) -> float:
    """
    スカラ/Series/DataFrame/Index などから float を1つ取り出す。
    失敗時は NaN。
    """
    try:
        if x is None:
            return float("nan")
        if isinstance(x, (pd.Series, pd.Index)):
            if len(x) == 0:
                return float("nan")
            return float(pd.to_numeric(pd.Series(x).iloc[-1], errors="coerce"))
        if isinstance(x, pd.DataFrame):
            if x.shape[1] == 0 or len(x) == 0:
                return float("nan")
            col = x.columns[-1]
            return float(pd.to_numeric(x[col].iloc[-1], errors="coerce"))
        return float(x)
    except Exception:
        return float("nan")


def _nan_to_none(x):
    if isinstance(x, (float, int)) and x != x:  # NaN
        return None
    return x


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(x)
    except Exception:
        return lo
    if not np.isfinite(v):
        return lo
    return float(max(lo, min(hi, v)))


def _to_float_or_none(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if not np.isfinite(v):
        return None
    return v


def _to_int_or_none(x: Any) -> Optional[int]:
    if x is None:
        return None
    try:
        v = int(x)
    except Exception:
        return None
    return v


def _compute_behavior_confidence(
    n: Optional[int],
    win_rate: Optional[float],
    avg_pl: Optional[float],
) -> float:
    """
    精度重視版 confidence

    conf_n        = clamp(n / 20, 0..1)
    conf_winrate  = clamp((win_rate - 0.45) / 0.15, 0..1)
    conf_pl       = clamp(avg_pl / 5000, 0..1)

    confidence = 0.5*conf_n + 0.3*conf_winrate + 0.2*conf_pl
    """
    nn = _to_int_or_none(n)
    wr = _to_float_or_none(win_rate)
    pl = _to_float_or_none(avg_pl)

    conf_n = _clamp((nn or 0) / 20.0, 0.0, 1.0)

    # win_rate は 0..1 想定。もし 0..100 で来ても壊さない
    if wr is not None and wr > 1.0:
        wr = wr / 100.0
    conf_win = _clamp(((wr or 0.0) - 0.45) / 0.15, 0.0, 1.0)

    conf_pl = _clamp((pl or 0.0) / 5000.0, 0.0, 1.0)

    conf = 0.5 * conf_n + 0.3 * conf_win + 0.2 * conf_pl
    return float(_clamp(conf, 0.0, 1.0))


def _blend_stars(
    stars_model: int,
    stars_behavior: int,
    confidence: float,
) -> int:
    """
    final = (1-conf)*model + conf*behavior
    roundして 1..5
    """
    sm = int(max(1, min(5, int(stars_model))))
    sb = int(max(1, min(5, int(stars_behavior))))
    c = _clamp(confidence, 0.0, 1.0)
    v = (1.0 - c) * sm + c * sb
    out = int(round(v))
    return int(max(1, min(5, out)))


def _build_reasons_features(feat: pd.DataFrame, last: float, atr: float) -> Dict[str, Any]:
    """
    reasons.make_reasons 用に、features DataFrame から必要な指標だけ抜き出して
    名前を合わせた dict を組み立てる。
    """
    if feat is None or len(feat) == 0:
        return {}

    row = feat.iloc[-1]

    def g(key: str) -> Optional[float]:
        try:
            v = row.get(key)
        except Exception:
            v = None
        if v is None:
            return None
        try:
            f = float(v)
        except Exception:
            return None
        if not np.isfinite(f):
            return None
        return f

    # トレンド傾き（中期）
    ema_slope = g("SLOPE_25") or g("SLOPE_20")

    # 相対強度は「20日リターン」を簡易的に％換算して使う
    rel_strength_10 = None
    r20 = g("RET_20")
    if r20 is not None:
        rel_strength_10 = r20 * 100.0  # 例: 0.12 → 12%

    # 直近1日の変動率（イベント検出用）
    ret1_pct = None
    r1 = g("RET_1")
    if r1 is not None:
        ret1_pct = r1 * 100.0  # 例: 0.08 → 8%

    # RSI
    rsi14 = g("RSI14")

    # 出来高と平均（Volume / MA25 でざっくり）
    vol = g("Volume")
    ma_base = g("MA25") or g("MA20")
    vol_ma_ratio = None
    if vol is not None and ma_base is not None and ma_base > 0:
        # 本来は出来高MAを使うのがベストだが、現状は価格MAを目安として使用
        vol_ma_ratio = vol / ma_base

    # ブレイクフラグ（ゴールデンクロス）
    breakout_flag = 0
    gcross = g("GCROSS")
    if gcross is not None and gcross > 0:
        breakout_flag = 1

    # VWAP乖離
    vwap_proximity = g("VWAP_GAP_PCT")

    # ATR
    atr14 = None
    if np.isfinite(atr):
        atr14 = float(atr)

    # 終値
    last_price = None
    if np.isfinite(last):
        last_price = float(last)

    return {
        "ema_slope": ema_slope,
        "rel_strength_10": rel_strength_10,
        "ret1_pct": ret1_pct,             # ★ イベント検出用（前日比％）
        "rsi14": rsi14,
        "vol_ma_ratio": vol_ma_ratio,     # ★ 新しい名前に統一
        "breakout_flag": breakout_flag,
        "atr14": atr14,
        "vwap_proximity": vwap_proximity,
        "last_price": last_price,
    }


def _extract_chart_ohlc(
    raw: pd.DataFrame,
    max_points: int = 60,
) -> Tuple[
    Optional[List[float]],
    Optional[List[float]],
    Optional[List[float]],
    Optional[List[float]],
    Optional[List[str]],
]:
    """
    チャート用の OHLC 配列＋日付配列を生成（ローソク足＋終値ライン＋X軸の日付表示用）。
    get_prices が返す DataFrame の末尾から max_points 本だけ抜き出す。
    """
    if raw is None:
        return None, None, None, None, None
    try:
        df = raw.copy()
    except Exception:
        return None, None, None, None, None

    if len(df) == 0:
        return None, None, None, None, None

    # 列名のゆらぎに軽く対応
    def col_name(candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    col_o = col_name(["Open", "open", "OPEN"])
    col_h = col_name(["High", "high", "HIGH"])
    col_l = col_name(["Low", "low", "LOW"])
    col_c = col_name(["Close", "close", "CLOSE"])

    if not (col_o and col_h and col_l and col_c):
        return None, None, None, None, None

    # 末尾 max_points 本
    df = df[[col_o, col_h, col_l, col_c]].tail(max_points)

    opens = [float(v) for v in df[col_o].tolist()]
    highs = [float(v) for v in df[col_h].tolist()]
    lows = [float(v) for v in df[col_l].tolist()]
    closes = [float(v) for v in df[col_c].tolist()]

    if not closes:
        return None, None, None, None, None

    # X軸用の日付（YYYY-MM-DD）
    dates: List[str] = []
    try:
        if isinstance(df.index, pd.DatetimeIndex):
            dates = [d.strftime("%Y-%m-%d") for d in df.index]
        else:
            # 念のため index を日時に解釈できるものだけ変換
            idx_dt = pd.to_datetime(df.index, errors="coerce")
            for d in idx_dt:
                if pd.isna(d):
                    dates.append("")  # 軽いフォールバック
                else:
                    dates.append(d.strftime("%Y-%m-%d"))
    except Exception:
        dates = []

    return opens, highs, lows, closes, (dates or None)


# =========================================================
# フォールバック実装（サービスが無い場合）
# =========================================================

def _fallback_score_sample(feat: pd.DataFrame) -> float:
    """
    0.0〜1.0 のスコアに正規化する簡易ロジック（テスト用）。
    """
    if feat is None or len(feat) == 0:
        return 0.0

    f = feat.copy()
    for c in ["RSI14", "RET_5", "RET_20", "SLOPE_5", "SLOPE_20"]:
        if c not in f.columns:
            f[c] = np.nan

    def nz(s: pd.Series) -> pd.Series:
        s = _safe_series(s)
        if s.empty:
            return s
        m = float(s.mean())
        sd = float(s.std(ddof=0))
        if not np.isfinite(sd) or sd == 0:
            return pd.Series(np.zeros(len(s)), index=s.index)
        return (s - m) / sd

    def sig(v: float) -> float:
        try:
            return float(1.0 / (1.0 + np.exp(-float(v))))
        except Exception:
            return 0.5

    rsi = _safe_float(nz(f["RSI14"]).iloc[-1])
    mom5 = _safe_float(nz(f["RET_5"]).iloc[-1])
    mom20 = _safe_float(nz(f["RET_20"]).iloc[-1])
    sl5 = _safe_float(nz(f["SLOPE_5"]).iloc[-1])
    sl20 = _safe_float(nz(f["SLOPE_20"]).iloc[-1])

    comp = (
        0.30 * sig(rsi)
        + 0.25 * sig(mom5)
        + 0.20 * sig(mom20)
        + 0.15 * sig(sl5)
        + 0.10 * sig(sl20)
    )
    return float(max(0.0, min(1.0, comp)))


def _fallback_stars(score01: float) -> int:
    if not np.isfinite(score01):
        return 1
    s = max(0.0, min(1.0, float(score01)))
    if s < 0.2:
        return 1
    if s < 0.4:
        return 2
    if s < 0.6:
        return 3
    if s < 0.8:
        return 4
    return 5


def _fallback_entry_tp_sl(last: float, atr: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    暫定・短期×攻め用の Entry / TP / SL。
    """
    if not np.isfinite(last) or not np.isfinite(atr) or atr <= 0:
        return None, None, None
    entry = last + 0.05 * atr
    tp = entry + 0.80 * atr
    sl = entry - 0.60 * atr
    return float(entry), float(tp), float(sl)


def _score_to_0_100(s01: float) -> int:
    if not np.isfinite(s01):
        return 0
    return int(round(max(0.0, min(1.0, s01)) * 100))


def _normalize_code(code: str) -> str:
    """
    DB/JSON でぶれないように銘柄コードを正規化。
    - "7203.T" → "7203"
    - "7203"   → "7203"
    """
    s = str(code or "").strip()
    if not s:
        return s
    if s.endswith(".T"):
        s = s[:-2]
    return s


def _mode_period_from_horizon(horizon: str) -> str:
    """
    picks_build の horizon を BehaviorStats の mode_period に合わせる。
      short/mid/long はそのまま
      それ以外は short 扱いに寄せる（壊さない）
    """
    h = (horizon or "").strip().lower()
    if h in ("short", "mid", "long"):
        return h
    return "short"


def _mode_aggr_from_style(style: str) -> str:
    """
    picks_build の style を BehaviorStats の mode_aggr に合わせる。
      aggressive -> aggr
      normal     -> norm
      defensive  -> def
    既に aggr/norm/def が来た場合はそのまま
    """
    s = (style or "").strip().lower()
    if s in ("aggr", "norm", "def"):
        return s
    if s in ("aggressive", "attack", "atk"):
        return "aggr"
    if s in ("normal", "standard", "norm"):
        return "norm"
    if s in ("defensive", "defence", "def"):
        return "def"
    # 既存 default に寄せる（壊さない）
    return "aggr"


# =========================================================
# 出力アイテム
# =========================================================

@dataclass
class PickItem:
    code: str
    name: Optional[str] = None
    sector_display: Optional[str] = None

    # チャート用 OHLC（最新 max_points 本）
    chart_open: Optional[List[float]] = None
    chart_high: Optional[List[float]] = None
    chart_low: Optional[List[float]] = None
    chart_closes: Optional[List[float]] = None  # 終値のみ（ライン用）
    chart_dates: Optional[List[str]] = None     # X軸用日付（YYYY-MM-DD）

    # テクニカル系オーバーレイ
    chart_ma_short: Optional[List[Optional[float]]] = None  # 例: MA5
    chart_ma_mid: Optional[List[Optional[float]]] = None    # 例: MA25
    chart_ma_75: Optional[List[Optional[float]]] = None     # MA75
    chart_ma_100: Optional[List[Optional[float]]] = None    # MA100
    chart_ma_200: Optional[List[Optional[float]]] = None    # MA200
    chart_vwap: Optional[List[Optional[float]]] = None      # VWAP
    chart_rsi: Optional[List[Optional[float]]] = None       # RSI14

    # 52週高安値 / 上場来高安値
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    high_all: Optional[float] = None
    low_all: Optional[float] = None

    last_close: Optional[float] = None
    atr: Optional[float] = None

    entry: Optional[float] = None
    tp: Optional[float] = None
    sl: Optional[float] = None

    score: Optional[float] = None          # 0..1
    score_100: Optional[int] = None        # 0..100
    stars: Optional[int] = None            # 1..5

    qty_rakuten: Optional[int] = None
    required_cash_rakuten: Optional[float] = None
    est_pl_rakuten: Optional[float] = None
    est_loss_rakuten: Optional[float] = None

    qty_matsui: Optional[int] = None
    required_cash_matsui: Optional[float] = None
    est_pl_matsui: Optional[float] = None
    est_loss_matsui: Optional[float] = None

    qty_sbi: Optional[int] = None
    required_cash_sbi: Optional[float] = None
    est_pl_sbi: Optional[float] = None
    est_loss_sbi: Optional[float] = None

    # sizing_service 側で組んだ共通メッセージ（両方0株など）
    reasons_text: Optional[List[str]] = None

    # 理由5つ＋懸念（reasons サービス）
    reason_lines: Optional[List[str]] = None
    reason_concern: Optional[str] = None

    # 証券会社別の見送り理由（qty=0 のときだけ使用）
    reason_rakuten: Optional[str] = None
    reason_matsui: Optional[str] = None
    reason_sbi: Optional[str] = None


# =========================================================
# 1銘柄処理
# =========================================================

def _work_one(
    user,
    code: str,
    nbars: int,
    *,
    mode_period: str,
    mode_aggr: str,
    behavior_map_primary: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
    behavior_map_all: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
    filter_stats: Optional[Dict[str, int]] = None,
    regime: Optional[object] = None,
) -> Optional[Tuple[PickItem, Dict[str, Any]]]:
    """
    単一銘柄について、価格→特徴量→スコア→⭐️→Entry/TP/SL→Sizing→理由 まで全部まとめて計算。
    sizing_meta には risk_pct / lot_size を入れて返す。

    ★本番仕様（⭐️）: 精度重視ハイブリッド
      - 市場適合⭐️（scoring_service）と、実績適合⭐️（BehaviorStats）を
        confidence（n, win_rate, avg_pl）でブレンドする。
      - BehaviorStats が無い銘柄は市場適合⭐️のみ（安全）。
      - BehaviorStats はまず (code, mode_period, mode_aggr) を見て、無ければ (code, all, all) を見る。
    """
    try:
        raw = get_prices(code, nbars=nbars, period="3y")
        if raw is None or len(raw) == 0:
            if BUILD_LOG:
                print(f"[picks_build] {code}: empty price")
            return None

        # チャート用 OHLC（ローソク足＋終値ライン＋日付）
        max_points = 260
        chart_open, chart_high, chart_low, chart_closes, chart_dates = _extract_chart_ohlc(
            raw, max_points=max_points
        )

        # 特徴量（MA / RSI / VWAP 等）
        cfg = FeatureConfig()
        feat = make_features(raw, cfg=cfg)
        if feat is None or len(feat) == 0:
            if BUILD_LOG:
                print(f"[picks_build] {code}: empty features")
            return None

        close_s = _safe_series(feat.get("Close"))
        atr_s = _safe_series(feat.get(f"ATR{cfg.atr_period}") if f"ATR{cfg.atr_period}" in feat else None)

        last = _safe_float(close_s.iloc[-1] if len(close_s) else np.nan)
        atr = _safe_float(atr_s.iloc[-1] if len(atr_s) else np.nan)

        # --- MA 系オーバーレイ ---
        ma_short_col = f"MA{cfg.ma_short}"    # 5
        ma_mid_col = f"MA{cfg.ma_mid}"        # 25
        ma_75_col = f"MA{cfg.ma_long}"        # 75
        ma_100_col = f"MA{cfg.ma_extra1}"     # 100
        ma_200_col = f"MA{cfg.ma_extra2}"     # 200
        rsi_col = f"RSI{cfg.rsi_period}"

        chart_ma_short = _series_tail_to_list(feat.get(ma_short_col), max_points=max_points)
        chart_ma_mid = _series_tail_to_list(feat.get(ma_mid_col), max_points=max_points)
        chart_ma_75 = _series_tail_to_list(feat.get(ma_75_col), max_points=max_points)
        chart_ma_100 = _series_tail_to_list(feat.get(ma_100_col), max_points=max_points)
        chart_ma_200 = _series_tail_to_list(feat.get(ma_200_col), max_points=max_points)
        chart_vwap = _series_tail_to_list(feat.get("VWAP"), max_points=max_points)
        chart_rsi = _series_tail_to_list(feat.get(rsi_col), max_points=max_points)

        # --- 52週高安値 / 上場来高安値（スカラー） ---
        high_52w = None
        low_52w = None
        high_all = None
        low_all = None

        if "HIGH_52W" in feat.columns:
            high_52w = _safe_float(_safe_series(feat["HIGH_52W"]).iloc[-1])
        if "LOW_52W" in feat.columns:
            low_52w = _safe_float(_safe_series(feat["LOW_52W"]).iloc[-1])
        if "HIGH_ALL" in feat.columns:
            high_all = _safe_float(_safe_series(feat["HIGH_ALL"]).iloc[-1])
        if "LOW_ALL" in feat.columns:
            low_all = _safe_float(_safe_series(feat["LOW_ALL"]).iloc[-1])

        high_52w = _nan_to_none(high_52w)
        low_52w = _nan_to_none(low_52w)
        high_all = _nan_to_none(high_all)
        low_all = _nan_to_none(low_all)

        # --- 仕手株・流動性などのフィルタリング層 ---
        if picks_check_all is not None and FilterContext is not None:
            try:
                ctx = FilterContext(
                    code=str(code),
                    feat=feat.iloc[-1].to_dict(),
                    last=last,
                    atr=atr,
                )
                decision = picks_check_all(ctx)
                if decision and getattr(decision, "skip", False):
                    # フィルタ理由ごとの件数カウント
                    if filter_stats is not None:
                        reason = getattr(decision, "reason_code", None) or "SKIP"
                        filter_stats[reason] = filter_stats.get(reason, 0) + 1

                    if BUILD_LOG:
                        rc = getattr(decision, "reason_code", None)
                        rt = getattr(decision, "reason_text", None)
                        print(f"[picks_build] {code}: filtered out ({rc}) {rt}")
                    return None
            except Exception as ex:
                if filter_stats is not None:
                    filter_stats["filter_error"] = filter_stats.get("filter_error", 0) + 1
                if BUILD_LOG:
                    print(f"[picks_build] {code}: filter error {ex}")

        # --- スコア（レジーム込み本格版） ---
        if ext_score_sample:
            try:
                # 新シグネチャ: score_sample(feat, regime=None)
                s01 = float(ext_score_sample(feat, regime=regime))
            except TypeError:
                # 万一、古いシグネチャだった場合のフォールバック
                s01 = float(ext_score_sample(feat))
        else:
            s01 = _fallback_score_sample(feat)

        score100 = _score_to_0_100(s01)

        # =========================================================
        # ⭐️（精度重視ハイブリッド）
        #   stars_model    : scoring_service（市場適合）
        #   stars_behavior : BehaviorStats（実績適合）
        #   confidence     : n / win_rate / avg_pl
        # =========================================================
        code_norm = _normalize_code(code)

        # まず市場適合⭐️（常に出せる）
        if ext_stars_from_score:
            try:
                stars_model = int(ext_stars_from_score(s01))
            except Exception:
                stars_model = _fallback_stars(s01)
        else:
            stars_model = _fallback_stars(s01)
        stars_model = int(max(1, min(5, stars_model)))

        # 次に実績適合⭐️（あれば使う）
        behavior_row: Optional[Dict[str, Any]] = None
        if behavior_map_primary is not None:
            behavior_row = behavior_map_primary.get((code_norm, mode_period, mode_aggr))

        # 無ければ all/all を見る
        if behavior_row is None and behavior_map_all is not None:
            behavior_row = behavior_map_all.get((code_norm, "all", "all"))

        stars: int = stars_model  # デフォは市場適合⭐️

        if behavior_row is not None:
            sb = behavior_row.get("stars")
            stars_behavior = _to_int_or_none(sb)

            # confidence用
            # まずよくある候補名から探す（モデル差分に強くする）
            n = (
                behavior_row.get("trade_count")
                if behavior_row.get("trade_count") is not None
                else behavior_row.get("n")
            )
            if n is None:
                n = behavior_row.get("count")

            win_rate = (
                behavior_row.get("win_rate")
                if behavior_row.get("win_rate") is not None
                else behavior_row.get("winrate")
            )
            avg_pl = (
                behavior_row.get("avg_pl")
                if behavior_row.get("avg_pl") is not None
                else behavior_row.get("avg_profit")
            )

            if isinstance(stars_behavior, int) and 1 <= stars_behavior <= 5:
                confidence = _compute_behavior_confidence(
                    _to_int_or_none(n),
                    _to_float_or_none(win_rate),
                    _to_float_or_none(avg_pl),
                )
                stars = _blend_stars(stars_model, stars_behavior, confidence)

                if BUILD_LOG:
                    mp = behavior_row.get("mode_period")
                    ma = behavior_row.get("mode_aggr")
                    print(
                        f"[picks_build] {code_norm} stars_hybrid="
                        f"{stars} (model={stars_model}, behavior={stars_behavior}, conf={confidence:.2f}, "
                        f"n={_to_int_or_none(n)}, wr={_to_float_or_none(win_rate)}, pl={_to_float_or_none(avg_pl)}, "
                        f"bs_mode={mp}/{ma})"
                    )
            else:
                # 実績レコードはあるが stars が壊れてる → 市場適合⭐️
                stars = stars_model
        else:
            # 実績が無い → 市場適合⭐️
            stars = stars_model

        # --- Entry / TP / SL ---
        if ext_entry_tp_sl:
            # picks_buildの style/horizon を使う（既存仕様）
            e, t, s = ext_entry_tp_sl(last, atr, mode="aggressive", horizon="short")
        else:
            e, t, s = _fallback_entry_tp_sl(last, atr)

        # --- 理由5つ＋懸念（特徴量ベース） ---
        reason_lines: Optional[List[str]] = None
        reason_concern: Optional[str] = None
        if make_ai_reasons is not None:
            try:
                reasons_feat = _build_reasons_features(feat, last, atr)
                rs, concern = make_ai_reasons(reasons_feat)
                if rs:
                    reason_lines = list(rs[:5])
                if concern:
                    reason_concern = str(concern)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] reasons error for {code}: {ex}")

        if BUILD_LOG:
            print(
                f"[picks_build] {code_norm} last={last} atr={atr} "
                f"score01={s01:.3f} score100={score100} stars={stars} "
                f"(period={mode_period} aggr={mode_aggr})"
            )

        item = PickItem(
            code=str(code_norm),
            last_close=_nan_to_none(last),
            atr=_nan_to_none(atr),
            entry=_nan_to_none(e),
            tp=_nan_to_none(t),
            sl=_nan_to_none(s),
            score=_nan_to_none(s01),
            score_100=int(score100),
            stars=int(stars),
            reason_lines=reason_lines,
            reason_concern=reason_concern,
            chart_open=chart_open,
            chart_high=chart_high,
            chart_low=chart_low,
            chart_closes=chart_closes,
            chart_dates=chart_dates,
            chart_ma_short=chart_ma_short,
            chart_ma_mid=chart_ma_mid,
            chart_ma_75=chart_ma_75,
            chart_ma_100=chart_ma_100,
            chart_ma_200=chart_ma_200,
            chart_vwap=chart_vwap,
            chart_rsi=chart_rsi,
            high_52w=high_52w,
            low_52w=low_52w,
            high_all=high_all,
            low_all=low_all,
        )

        # --- Sizing（数量・必要資金・想定PL/損失 + 見送り理由） ---
        sizing = compute_position_sizing(
            user=user,
            code=str(code_norm),
            last_price=last,
            atr=atr,
            entry=e,
            tp=t,
            sl=s,
        )

        # 楽天
        item.qty_rakuten = sizing.get("qty_rakuten")
        item.required_cash_rakuten = sizing.get("required_cash_rakuten")
        item.est_pl_rakuten = sizing.get("est_pl_rakuten")
        item.est_loss_rakuten = sizing.get("est_loss_rakuten")

        # 松井
        item.qty_matsui = sizing.get("qty_matsui")
        item.required_cash_matsui = sizing.get("required_cash_matsui")
        item.est_pl_matsui = sizing.get("est_pl_matsui")
        item.est_loss_matsui = sizing.get("est_loss_matsui")

        # SBI
        item.qty_sbi = sizing.get("qty_sbi")
        item.required_cash_sbi = sizing.get("required_cash_sbi")
        item.est_pl_sbi = sizing.get("est_pl_sbi")
        item.est_loss_sbi = sizing.get("est_loss_sbi")

        # 共通メッセージ
        reasons_text = sizing.get("reasons_text")
        item.reasons_text = reasons_text if reasons_text else None

        # 証券会社別の見送り理由（0株のときにテンプレートが表示）
        item.reason_rakuten = sizing.get("reason_rakuten_msg") or ""
        item.reason_matsui = sizing.get("reason_matsui_msg") or ""
        item.reason_sbi = sizing.get("reason_sbi_msg") or ""

        sizing_meta = {
            "risk_pct": sizing.get("risk_pct"),
            "lot_size": sizing.get("lot_size"),
        }
        return item, sizing_meta

    except Exception as e:
        print(f"[picks_build] work error for {code}: {e}")
        if filter_stats is not None:
            filter_stats["work_error"] = filter_stats.get("work_error", 0) + 1
        return None


# =========================================================
# ユニバース読み込み
# =========================================================

def _load_universe_from_txt(name: str) -> List[str]:
    base = Path("aiapp/data/universe")
    filename = name
    if not filename.endswith(".txt"):
        filename = f"{filename}.txt"
    txt = base / filename
    if not txt.exists():
        print(f"[picks_build] universe file not found: {txt}")
        return []
    codes: List[str] = []
    for line in txt.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        codes.append(line.split(",")[0].strip())
    return codes


def _load_universe_all_jpx() -> List[str]:
    """
    StockMaster から日本株全銘柄コードを取る ALL-JPX 用。
    """
    if StockMaster is None:
        print("[picks_build] StockMaster not available; ALL-JPX empty")
        return []
    try:
        qs = StockMaster.objects.values_list("code", flat=True).order_by("code")
        codes = [str(c).strip() for c in qs if c]
        print(f"[picks_build] ALL-JPX from StockMaster: {len(codes)} codes")
        return codes
    except Exception as e:
        print(f"[picks_build] ALL-JPX load error: {e}")
        return []


def _load_universe(name: str) -> List[str]:
    """
    ユニバース名 → 銘柄コード一覧。
      all_jpx / all / jpx_all         → StockMaster から全件
      nk225 / nikkei225 / nikkei_225  → data/universe/nk225.txt
      それ以外                          → data/universe/<name>.txt
    """
    key = (name or "").strip().lower()

    if key in ("all_jpx", "all", "jpx_all"):
        codes = _load_universe_all_jpx()
        if codes:
            return codes
        print("[picks_build] ALL-JPX fallback to txt")
        return _load_universe_from_txt("all_jpx")

    if key in ("nk225", "nikkei225", "nikkei_225"):
        return _load_universe_from_txt("nk225")

    return _load_universe_from_txt(key)


# =========================================================
# 銘柄名・業種補完
# =========================================================

def _enrich_meta(items: List[PickItem]) -> None:
    if not items or StockMaster is None:
        return
    codes = [it.code for it in items if it and it.code]
    if not codes:
        return
    try:
        qs = StockMaster.objects.filter(code__in=codes).values("code", "name", "sector_name")
        meta: Dict[str, Tuple[str, str]] = {
            str(r["code"]): (r.get("name") or "", r.get("sector_name") or "")
            for r in qs
        }
        for it in items:
            if it.code in meta:
                nm, sec = meta[it.code]
                if not it.name:
                    it.name = nm or None
                if not it.sector_display:
                    it.sector_display = sec or None
    except Exception:
        pass


# =========================================================
# Django management command
# =========================================================

class Command(BaseCommand):
    help = "AIピック生成（FULL + TopK + Sizing + 理由テキスト）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--universe",
            type=str,
            default="nk225",
            help="all_jpx / nk225 / nikkei_225 / <file name> など",
        )
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=None)
        parser.add_argument("--nbars", type=int, default=260)
        parser.add_argument("--nbars-lite", type=int, default=45)
        parser.add_argument("--use-snapshot", action="store_true")
        parser.add_argument("--lite-only", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--style", type=str, default="aggressive")
        parser.add_argument("--horizon", type=str, default="short")
        parser.add_argument(
            "--topk",
            type=int,
            default=int(os.getenv("AIAPP_TOPK", "10")),
            help="上位何銘柄を latest_full.json に出すか",
        )

    def handle(self, *args, **opts):
        universe = opts.get("universe") or "nk225"
        nbars = int(opts.get("nbars") or 260)
        style = (opts.get("style") or "aggressive").lower()
        horizon = (opts.get("horizon") or "short").lower()
        topk = int(opts.get("topk") or 10)

        # ★ 本番仕様（⭐️）キー
        mode_period = _mode_period_from_horizon(horizon)
        mode_aggr = _mode_aggr_from_style(style)

        codes = _load_universe(universe)
        stockmaster_total = len(codes)

        # ---- マクロレジームの読み込み（あれば）----
        macro_regime = None
        if MacroRegimeSnapshot is not None:
            try:
                today = datetime.now(JST).date()
                macro_regime = (
                    MacroRegimeSnapshot.objects
                    .filter(date__lte=today)
                    .order_by("-date")
                    .first()
                )
                if BUILD_LOG and macro_regime is not None:
                    print(
                        f"[picks_build] use MacroRegimeSnapshot "
                        f"date={macro_regime.date} regime={macro_regime.regime_label}"
                    )
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] macro regime load error: {ex}")

        # ★ BehaviorStats をまとめて引く（primary: period/aggr, fallback: all/all）
        behavior_map_primary: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        behavior_map_all: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

        if BehaviorStats is not None and codes:
            codes_norm = [_normalize_code(c) for c in codes if c]

            # ---- primary（mode_period/mode_aggr）----
            try:
                # まず“拡張フィールド”込みで試す（無ければexceptで落として最小にする）
                qs = (
                    BehaviorStats.objects
                    .filter(code__in=codes_norm, mode_period=mode_period, mode_aggr=mode_aggr)
                    .values(
                        "code", "mode_period", "mode_aggr", "stars",
                        "trade_count", "n", "count",
                        "win_rate", "winrate",
                        "avg_pl", "avg_profit",
                    )
                )
                for r in qs:
                    c = _normalize_code(r.get("code"))
                    mp = (r.get("mode_period") or "").strip().lower()
                    ma = (r.get("mode_aggr") or "").strip().lower()
                    if c and mp and ma:
                        behavior_map_primary[(c, mp, ma)] = dict(r)
            except Exception:
                try:
                    qs = (
                        BehaviorStats.objects
                        .filter(code__in=codes_norm, mode_period=mode_period, mode_aggr=mode_aggr)
                        .values("code", "mode_period", "mode_aggr", "stars")
                    )
                    for r in qs:
                        c = _normalize_code(r.get("code"))
                        mp = (r.get("mode_period") or "").strip().lower()
                        ma = (r.get("mode_aggr") or "").strip().lower()
                        if c and mp and ma:
                            behavior_map_primary[(c, mp, ma)] = dict(r)
                except Exception as ex:
                    if BUILD_LOG:
                        print(f"[picks_build] BehaviorStats primary load error: {ex}")

            # ---- all/all（fallback）----
            try:
                qs_all = (
                    BehaviorStats.objects
                    .filter(code__in=codes_norm, mode_period="all", mode_aggr="all")
                    .values(
                        "code", "mode_period", "mode_aggr", "stars",
                        "trade_count", "n", "count",
                        "win_rate", "winrate",
                        "avg_pl", "avg_profit",
                    )
                )
                for r in qs_all:
                    c = _normalize_code(r.get("code"))
                    mp = (r.get("mode_period") or "").strip().lower()
                    ma = (r.get("mode_aggr") or "").strip().lower()
                    if c and mp and ma:
                        behavior_map_all[(c, mp, ma)] = dict(r)
            except Exception:
                try:
                    qs_all = (
                        BehaviorStats.objects
                        .filter(code__in=codes_norm, mode_period="all", mode_aggr="all")
                        .values("code", "mode_period", "mode_aggr", "stars")
                    )
                    for r in qs_all:
                        c = _normalize_code(r.get("code"))
                        mp = (r.get("mode_period") or "").strip().lower()
                        ma = (r.get("mode_aggr") or "").strip().lower()
                        if c and mp and ma:
                            behavior_map_all[(c, mp, ma)] = dict(r)
                except Exception as ex:
                    if BUILD_LOG:
                        print(f"[picks_build] BehaviorStats all/all load error: {ex}")

            if BUILD_LOG:
                print(
                    f"[picks_build] BehaviorStats loaded: "
                    f"primary={len(behavior_map_primary)} rows (period={mode_period} aggr={mode_aggr}) "
                    f"+ all/all={len(behavior_map_all)} rows"
                )

        # 空ユニバースのとき
        if not codes:
            print("[picks_build] universe empty → 空JSON出力")

            regime_date_str = None
            if macro_regime is not None:
                d = getattr(macro_regime, "date", None)
                if d is not None:
                    regime_date_str = d.isoformat()

            self._emit(
                [],
                [],
                mode="full",
                style=style,
                horizon=horizon,
                universe=universe,
                topk=topk,
                meta_extra={
                    "stockmaster_total": stockmaster_total,
                    "filter_stats": {},
                    "regime_date": regime_date_str,
                    "regime_label": getattr(macro_regime, "regime_label", None) if macro_regime else None,
                    "regime_summary": getattr(macro_regime, "summary", None) if macro_regime else None,
                    # ★ 追加メタ（UI/デバッグ用）
                    "stars_mode_period": mode_period,
                    "stars_mode_aggr": mode_aggr,
                    "behaviorstats_primary_rows": len(behavior_map_primary),
                    "behaviorstats_all_rows": len(behavior_map_all),
                },
            )
            return

        if BUILD_LOG:
            print(f"[picks_build] start FULL universe={universe} codes={stockmaster_total}")

        User = get_user_model()
        user = User.objects.first()

        items: List[PickItem] = []
        meta_extra: Dict[str, Any] = {}

        # フィルタ理由ごとの削除件数カウンタ
        filter_stats: Dict[str, int] = {}

        for code in codes:
            res = _work_one(
                user,
                code,
                nbars=nbars,
                mode_period=mode_period,
                mode_aggr=mode_aggr,
                behavior_map_primary=behavior_map_primary,
                behavior_map_all=behavior_map_all,
                filter_stats=filter_stats,
                regime=macro_regime,
            )
            if res is None:
                continue
            item, sizing_meta = res
            items.append(item)

            # meta（risk_pct / lot_size）は最初に取得できた値を採用
            if sizing_meta:
                if sizing_meta.get("risk_pct") is not None and "risk_pct" not in meta_extra:
                    meta_extra["risk_pct"] = float(sizing_meta["risk_pct"])
                if sizing_meta.get("lot_size") is not None and "lot_size" not in meta_extra:
                    meta_extra["lot_size"] = int(sizing_meta["lot_size"])

        _enrich_meta(items)

        # ---- セクターバイアス・サイズバイアス適用（あれば） ----
        if apply_bias_all is not None and items:
            try:
                apply_bias_all(items)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] bias error: {ex}")

        # 並び: score_100 desc → last_close desc
        items.sort(
            key=lambda x: (
                x.score_100 if x.score_100 is not None else -1,
                x.last_close if x.last_close is not None else -1,
            ),
            reverse=True,
        )

        top_items = items[: max(0, topk)]

        if BUILD_LOG:
            print(
                f"[picks_build] done stockmaster_total={stockmaster_total} "
                f"total={len(items)} topk={len(top_items)}"
            )

        # 追加メタ（総StockMaster件数 & フィルタ別削除件数 & レジーム情報）
        meta_extra["stockmaster_total"] = stockmaster_total
        meta_extra["filter_stats"] = filter_stats

        if macro_regime is not None:
            d = getattr(macro_regime, "date", None)
            regime_date_str = d.isoformat() if d is not None else None
            meta_extra["regime_date"] = regime_date_str
            meta_extra["regime_label"] = getattr(macro_regime, "regime_label", None)
            meta_extra["regime_summary"] = getattr(macro_regime, "summary", None)

        # ★ ⭐️本番仕様メタ
        meta_extra["stars_mode_period"] = mode_period
        meta_extra["stars_mode_aggr"] = mode_aggr
        meta_extra["behaviorstats_primary_rows"] = len(behavior_map_primary)
        meta_extra["behaviorstats_all_rows"] = len(behavior_map_all)

        self._emit(
            items,
            top_items,
            mode="full",
            style=style,
            horizon=horizon,
            universe=universe,
            topk=topk,
            meta_extra=meta_extra,
        )

    # -------------------- 出力 --------------------

    def _emit(
        self,
        all_items: List[PickItem],
        top_items: List[PickItem],
        *,
        mode: str,
        style: str,
        horizon: str,
        universe: str,
        topk: int,
        meta_extra: Dict[str, Any],
    ) -> None:
        meta: Dict[str, Any] = {
            "mode": mode,
            "style": style,
            "horizon": horizon,
            "universe": universe,
            "total": len(all_items),
            "topk": topk,
        }
        meta.update({k: v for k, v in (meta_extra or {}).items() if v is not None})

        data_all = {"meta": meta, "items": [asdict(x) for x in all_items]}
        data_top = {"meta": meta, "items": [asdict(x) for x in top_items]}

        PICKS_DIR.mkdir(parents=True, exist_ok=True)

        # 全件（検証用）
        out_all_latest = PICKS_DIR / "latest_full_all.json"
        out_all_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full_all.json"
        out_all_latest.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))
        out_all_stamp.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))

        # TopK（UI用）
        out_top_latest = PICKS_DIR / "latest_full.json"
        out_top_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full.json"
        out_top_latest.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))
        out_top_stamp.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))