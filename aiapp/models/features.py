# aiapp/models/features.py
# -*- coding: utf-8 -*-
"""
aiapp.models.features
特徴量を計算するモジュール（個別銘柄1本の終値/高安/出来高系列から算出）。

提供関数:
- compute_features(df, benchmark_df=None) -> DataFrame
  df: 必須。index=DatetimeIndex, columns=["Open","High","Low","Close","Volume"]
  benchmark_df: 任意。ベンチマーク指数（日経/Topix等）。同形式を想定。

返すDataFrame（主な列）:
- MA5, MA25, MA75, MA100, MA200
- BBU, BBM, BBL, BB_Z
- RSI14 / MACD, MACD_SIGNAL, MACD_HIST
- ATR14
- VWAP, VWAP_GAP_PCT  ※VWAPは日中リセット型（本物寄り）
- RET_1, RET_5, RET_20
- SLOPE_5, SLOPE_25
- GCROSS, DCROSS
- HIGH_52W, LOW_52W, HIGH_ALL, LOW_ALL
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd


# ========= 列名ゆらぎ対策 & 基本ヘルパ =========

def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    yfinance等で発生する MultiIndex 列をフラット化する。
    例: ('Close','7974.T') -> 'Close_7974.T' -> 小文字比較用に .lower() で照合。
    """
    if isinstance(df.columns, pd.MultiIndex):
        flat = []
        for col in df.columns:
            # col はタプル想定。None/空を除き '_' 連結
            parts = [str(x) for x in col if x is not None and str(x) != ""]
            flat.append("_".join(parts))
        df = df.copy()
        df.columns = flat
    return df


def _normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    列名の大小文字・別名を正規化し、最終的に ["Open","High","Low","Close","Volume"] を揃える。
    - 代表的別名: 'adj close','adj_close','adjclose','price','last','last_close' などを 'Close' に寄せる
    - 'vol','v' は 'Volume'
    - 数値化（to_numeric）、NaT除去、重複日付の後勝ち解消、昇順ソート
    """
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)

    df = _flatten_columns(df)

    # 現在の列 -> 原名, 小文字名
    original_cols = list(df.columns)
    low2orig = {str(c).strip().lower(): c for c in original_cols}

    # マッピング候補
    cand = {
        "open":  ["open", "o"],
        "high":  ["high", "h"],
        "low":   ["low", "l"],
        "close": ["close", "c", "adj close", "adj_close", "adjclose", "price", "last", "last_close"],
        "volume": ["volume", "vol", "v"],
    }

    def pick(col_key: str):
        for alias in cand[col_key]:
            if alias in low2orig:
                return low2orig[alias]
            # MultiIndexフラット化後に 'close_XXXX' などを 'close' として拾いたい場合:
            if any(k.startswith(alias + "_") for k in low2orig.keys()):
                # 最初に見つかった close_* を採用
                return next(k for k in low2orig.keys() if k.startswith(alias + "_"))
        return None

    # 見つかった実列名（見つからなければ None）
    col_open = pick("open")
    col_high = pick("high")
    col_low = pick("low")
    col_close = pick("close")
    col_vol = pick("volume")

    out = pd.DataFrame(index=df.index.copy())
    # 各列をコピー（無ければ NaN 列）
    out["Open"] = pd.to_numeric(df[col_open], errors="coerce") if col_open is not None else np.nan
    out["High"] = pd.to_numeric(df[col_high], errors="coerce") if col_high is not None else np.nan
    out["Low"] = pd.to_numeric(df[col_low], errors="coerce") if col_low is not None else np.nan
    out["Close"] = pd.to_numeric(df[col_close], errors="coerce") if col_close is not None else np.nan
    out["Volume"] = pd.to_numeric(df[col_vol], errors="coerce") if col_vol is not None else np.nan

    # Index を Datetime に
    idx = pd.to_datetime(out.index, errors="coerce")
    mask = ~idx.isna()
    out = out.loc[mask]
    out.index = idx[mask]

    # 同一日重複は後勝ち
    out = out[~out.index.duplicated(keep="last")]
    out = out.sort_index()
    return out


def _ensure_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    最低限の列と DatetimeIndex を保証し、["Open","High","Low","Close","Volume"] のみ返す。
    - 列名ゆらぎ吸収（大文字/小文字、adj close等）
    - 数値化 / 日付整形 / 重複解消 / ソート
    """
    df = _normalize_ohlcv_columns(df)
    need = ["Open", "High", "Low", "Close", "Volume"]
    for c in need:
        if c not in df.columns:
            df[c] = np.nan
    return df[need].copy()


def _safe_pct_change(s: pd.Series, periods: int = 1) -> pd.Series:
    """NaN/inf暴発を避けた%変化（pandas将来変更に備えfill_method=Noneを明示）。"""
    s = s.astype("float64")
    return s.pct_change(periods=periods, fill_method=None).replace([np.inf, -np.inf], np.nan)


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def _sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window, min_periods=window).mean()


def _std(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window, min_periods=window).std()


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = _true_range(high, low, close)
    return _ema(tr, period)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    ma_up = _ema(up, period)
    ma_down = _ema(down, period)
    rs = ma_up / ma_down.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = _ema(close, fast) - _ema(close, slow)
    signal_line = _ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _bollinger(close: pd.Series, window: int = 20, n_sigma: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ma = _sma(close, window)
    sd = _std(close, window)
    upper = ma + n_sigma * sd
    lower = ma - n_sigma * sd
    return upper, ma, lower


def _zscore(s: pd.Series, window: int = 20) -> pd.Series:
    mu = _sma(s, window)
    sd = _std(s, window)
    return (s - mu) / sd.replace(0, np.nan)


def _slope(s: pd.Series, window: int = 5) -> pd.Series:
    """
    単回帰の傾き（窓内で t=0..n-1 に対する Close の傾き）。
    スケール不変にするため、標準化したxで計算（単位は/日）。
    """
    n = window
    if n < 2:
        return pd.Series(index=s.index, dtype="float64")

    # x は 0..n-1 を標準化
    x = np.arange(n, dtype="float64")
    x = (x - x.mean()) / (x.std(ddof=0) + 1e-12)

    def _fit(y: np.ndarray) -> float:
        if np.isnan(y).any():
            return np.nan
        y = (y - y.mean()) / (y.std(ddof=0) + 1e-12)
        # 傾き = 相関係数（x,y）に等しい
        return float(np.dot(x, y) / (n - 1))

    return s.rolling(window=n, min_periods=n).apply(
        lambda arr: _fit(np.asarray(arr, dtype="float64")), raw=True
    )


def _vwap(df: pd.DataFrame) -> pd.Series:
    """
    日中型VWAP（本物寄り）。
    - インデックスが分足/秒足などの場合: 「同じ日付内」で PV・出来高を累積 → その日のVWAP
    - 日足だけの場合: その日の TypicalPrice をベースにした1本VWAPに近い値
    どちらの場合も「日付が変わるとVWAPもリセット」される。
    """
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"]

    # 日付ごとに累積PV / 累積出来高を取る（インデックスがdatetime前提）
    dates = pd.to_datetime(df.index).date
    pv = tp * vol

    pv_cum = pv.groupby(dates).cumsum()
    vol_cum = vol.groupby(dates).cumsum().replace(0, np.nan)

    vwap = pv_cum / vol_cum
    return vwap.ffill()  # 日内での欠損を軽く埋める


@dataclass(frozen=True)
class FeatureConfig:
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_window: int = 20
    bb_sigma: float = 2.0
    atr_period: int = 14

    # MAの設定（日本株向けに 5/25/75/100/200）
    ma_short: int = 5
    ma_mid: int = 25
    ma_long: int = 75
    ma_extra1: int = 100
    ma_extra2: int = 200

    # 傾き用（短期・中期）
    slope_short: int = 5
    slope_mid: int = 25

    # 相対強度ON/OFF用フラグ（今はまだ未使用でもOK）
    enable_rel_strength_10: bool = False
    # どこかから benchmark_df=... が渡されても受け取れるようにするための器
    benchmark_df: object | None = None


def make_features(raw: pd.DataFrame, cfg: Optional[FeatureConfig] = None) -> pd.DataFrame:
    """
    主要テクニカル指標を付与して返すメイン関数。
    - 列名ゆらぎを吸収してから加工（Open/High/Low/Close/Volume を保証）
    - 将来の pandas 変更に備え pct_change(fill_method=None) を使用
    """
    cfg = cfg or FeatureConfig()
    df = _ensure_ohlcv(raw)

    # 欠損を軽く埋める（始値=終値、H/Lも埋め、出来高は0許容）
    df["Close"] = df["Close"].ffill()
    df["Open"] = df["Open"].fillna(df["Close"])
    df["High"] = df["High"].fillna(df[["Open", "Close"]].max(axis=1))
    df["Low"] = df["Low"].fillna(df[["Open", "Close"]].min(axis=1))
    df["Volume"] = df["Volume"].fillna(0)

    close = df["Close"].astype("float64")

    # --- 移動平均・ボリンジャー ---
    df[f"MA{cfg.ma_short}"] = _sma(close, cfg.ma_short)
    df[f"MA{cfg.ma_mid}"] = _sma(close, cfg.ma_mid)
    df[f"MA{cfg.ma_long}"] = _sma(close, cfg.ma_long)
    df[f"MA{cfg.ma_extra1}"] = _sma(close, cfg.ma_extra1)
    df[f"MA{cfg.ma_extra2}"] = _sma(close, cfg.ma_extra2)

    bb_u, bb_m, bb_l = _bollinger(close, cfg.bb_window, cfg.bb_sigma)
    df["BBU"], df["BBM"], df["BBL"] = bb_u, bb_m, bb_l
    df["BB_Z"] = _zscore(close, cfg.bb_window)

    # --- RSI / MACD / ATR ---
    df[f"RSI{cfg.rsi_period}"] = _rsi(close, cfg.rsi_period)
    macd_line, macd_signal, macd_hist = _macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    df["MACD"], df["MACD_SIGNAL"], df["MACD_HIST"] = macd_line, macd_signal, macd_hist
    df[f"ATR{cfg.atr_period}"] = _atr(df["High"], df["Low"], close, cfg.atr_period)

    # --- VWAP とその乖離 ---
    df["VWAP"] = _vwap(df)
    df["VWAP_GAP_PCT"] = (close / df["VWAP"] - 1) * 100.0

    # --- 収益率・傾き ---
    df["RET_1"] = _safe_pct_change(close, 1)
    df["RET_5"] = _safe_pct_change(close, 5)
    df["RET_20"] = _safe_pct_change(close, 20)

    df[f"SLOPE_{cfg.slope_short}"] = _slope(close, cfg.slope_short)
    df[f"SLOPE_{cfg.slope_mid}"] = _slope(close, cfg.slope_mid)

    # --- ゴールデンクロス/デッドクロスのフラグ（例：短中期）---
    ma_s, ma_m = df[f"MA{cfg.ma_short}"], df[f"MA{cfg.ma_mid}"]
    cross = (ma_s > ma_m).astype(int) - (ma_s.shift(1) > ma_m.shift(1)).astype(int)
    df["GCROSS"] = (cross == 1).astype(int)
    df["DCROSS"] = (cross == -1).astype(int)

    # --- 52週高安値 / 上場来高安値 ---
    # 52週 ≒ 252営業日で近似
    window_52w = 252
    df["HIGH_52W"] = close.rolling(window_52w, min_periods=1).max()
    df["LOW_52W"] = close.rolling(window_52w, min_periods=1).min()
    df["HIGH_ALL"] = close.cummax()
    df["LOW_ALL"] = close.cummin()

    # --- 最終の軽い欠損処理 ---
    price_cols = ["Open", "High", "Low", "Close", "VWAP", "BBU", "BBM", "BBL"]
    for c in price_cols:
        if c in df.columns:
            df[c] = df[c].ffill()

    indi_cols = [
        f"MA{cfg.ma_short}",
        f"MA{cfg.ma_mid}",
        f"MA{cfg.ma_long}",
        f"MA{cfg.ma_extra1}",
        f"MA{cfg.ma_extra2}",
        f"ATR{cfg.atr_period}",
        f"RSI{cfg.rsi_period}",
        "MACD",
        "MACD_SIGNAL",
        "MACD_HIST",
        "BB_Z",
        f"SLOPE_{cfg.slope_short}",
        f"SLOPE_{cfg.slope_mid}",
        "VWAP_GAP_PCT",
        "RET_1",
        "RET_5",
        "RET_20",
        "GCROSS",
        "DCROSS",
        "HIGH_52W",
        "LOW_52W",
        "HIGH_ALL",
        "LOW_ALL",
    ]
    for c in indi_cols:
        if c in df.columns:
            df[c] = df[c].ffill().bfill()

    return df


# ========= 代表的な単品API =========

def vwap_series(df: pd.DataFrame) -> pd.Series:
    """VWAP のみが必要なときの軽量API。"""
    df = _ensure_ohlcv(df)
    return _vwap(df)


def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 単独のヘルパ。"""
    return _rsi(close.astype("float64"), period)


def macd_series(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD のみを返す（列: macd, signal, hist）。"""
    m, s, h = _macd(close.astype("float64"), fast, slow, signal)
    return pd.DataFrame({"macd": m, "signal": s, "hist": h})


# 旧インターフェース名との互換ラッパ
def compute_features(df: pd.DataFrame, benchmark_df=None, cfg: Optional[FeatureConfig] = None) -> pd.DataFrame:
    """
    旧仕様互換ラッパー。
    benchmark_df は現在未使用（将来的に日経平均やTOPIXとの相対指標用）。
    """
    try:
        return make_features(df, cfg=cfg)
    except Exception as e:
        print(f"[compute_features] fallback error: {e}")
        return make_features(df)