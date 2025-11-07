"""
aiapp.models.features
特徴量を計算するモジュール（個別銘柄1本の終値/高安/出来高系列から算出）。

提供関数:
- compute_features(df, benchmark_df=None) -> dict
  df: 必須。index=DatetimeIndex, columns=["Open","High","Low","Close","Volume"]
  benchmark_df: 任意。ベンチマーク指数（日経/Topix等）。同形式を想定。

返す辞書（主なキー）:
- ema_fast, ema_slow, ema_slope
- rsi14, roc10
- vol_ma20_ratio
- atr14
- breakout_flag (直近高値更新)
- vwap_proximity (VWAP近接率)
- rel_strength_10 (ベンチ比リターン差)
"""

"""
価格時系列（日足 OHLCV）から特徴量を作るユーティリティ。
・入力: index=DatetimeIndex, columns=["Open","High","Low","Close","Volume"]
・出力: 上記に加えて各種テクニカル指標の列を付与した DataFrame を返す

pandas 2.3+ に準拠し、fillna(method="ffill") は使用せず .ffill() / .bfill() を用いる。
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd


# ========= 基本ヘルパ =========

def _ensure_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """最低限の列とDatetimeIndexを保証し、ソートして返す。"""
    need = ["Open", "High", "Low", "Close", "Volume"]
    for c in need:
        if c not in df.columns:
            df[c] = np.nan
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
    df = df.sort_index()
    return df[need].copy()


def _safe_pct_change(s: pd.Series, periods: int = 1) -> pd.Series:
    """0割り・NaN暴発を避けた対数リターン寄りの%変化（通常のpct_changeに近似）。"""
    s = s.astype("float64")
    return s.pct_change(periods=periods).replace([np.inf, -np.inf], np.nan)


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

    return s.rolling(window=n, min_periods=n).apply(lambda arr: _fit(np.asarray(arr, dtype="float64")), raw=True)


def _vwap(df: pd.DataFrame) -> pd.Series:
    """
    単純日足VWAP。日中のティックが無いので、近似として (H+L+C)/3 * Volume の累積で算出。
    """
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    pv = tp * df["Volume"]
    cum_pv = pv.cumsum()
    cum_vol = df["Volume"].cumsum().replace(0, np.nan)
    vwap = (cum_pv / cum_vol)
    return vwap.ffill()  # 旧: fillna(method="ffill")


@dataclass(frozen=True)
class FeatureConfig:
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_window: int = 20
    bb_sigma: float = 2.0
    atr_period: int = 14
    ma_short: int = 5
    ma_mid: int = 20
    ma_long: int = 50
    slope_short: int = 5
    slope_mid: int = 20


def make_features(raw: pd.DataFrame, cfg: Optional[FeatureConfig] = None) -> pd.DataFrame:
    """
    主要テクニカル指標を付与して返すメイン関数。
    """
    cfg = cfg or FeatureConfig()
    df = _ensure_ohlcv(raw)

    # 欠損を軽く埋める（始値=終値、H/Lも埋め、出来高は0許容）
    df["Close"] = df["Close"].ffill()
    df["Open"] = df["Open"].fillna(df["Close"])
    df["High"] = df["High"].fillna(df[["Open", "Close"]].max(axis=1))
    df["Low"] = df["Low"].fillna(df[["Open", "Close"]].min(axis=1))
    df["Volume"] = df["Volume"].fillna(0)

    # --- 移動平均・ボリンジャー ---
    df[f"MA{cfg.ma_short}"] = _sma(df["Close"], cfg.ma_short)
    df[f"MA{cfg.ma_mid}"] = _sma(df["Close"], cfg.ma_mid)
    df[f"MA{cfg.ma_long}"] = _sma(df["Close"], cfg.ma_long)

    bb_u, bb_m, bb_l = _bollinger(df["Close"], cfg.bb_window, cfg.bb_sigma)
    df["BBU"], df["BBM"], df["BBL"] = bb_u, bb_m, bb_l
    df["BB_Z"] = _zscore(df["Close"], cfg.bb_window)

    # --- RSI / MACD / ATR ---
    df[f"RSI{cfg.rsi_period}"] = _rsi(df["Close"], cfg.rsi_period)
    macd_line, macd_signal, macd_hist = _macd(df["Close"], cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    df["MACD"], df["MACD_SIGNAL"], df["MACD_HIST"] = macd_line, macd_signal, macd_hist
    df[f"ATR{cfg.atr_period}"] = _atr(df["High"], df["Low"], df["Close"], cfg.atr_period)

    # --- VWAP とその乖離 ---
    df["VWAP"] = _vwap(df)
    df["VWAP_GAP_PCT"] = (df["Close"] / df["VWAP"] - 1) * 100.0

    # --- 収益率・傾き ---
    df["RET_1"] = _safe_pct_change(df["Close"], 1)
    df["RET_5"] = _safe_pct_change(df["Close"], 5)
    df["RET_20"] = _safe_pct_change(df["Close"], 20)

    df[f"SLOPE_{cfg.slope_short}"] = _slope(df["Close"], cfg.slope_short)
    df[f"SLOPE_{cfg.slope_mid}"] = _slope(df["Close"], cfg.slope_mid)

    # --- ゴールデンクロス/デッドクロスのフラグ（例：短中期）---
    ma_s, ma_m = df[f"MA{cfg.ma_short}"], df[f"MA{cfg.ma_mid}"]
    cross = (ma_s > ma_m).astype(int) - (ma_s.shift(1) > ma_m.shift(1)).astype(int)
    df["GCROSS"] = (cross == 1).astype(int)
    df["DCROSS"] = (cross == -1).astype(int)

    # --- 最終の軽い欠損処理 ---
    price_cols = ["Open", "High", "Low", "Close", "VWAP", "BBU", "BBM", "BBL"]
    for c in price_cols:
        if c in df.columns:
            df[c] = df[c].ffill()

    indi_cols = [
        f"MA{cfg.ma_short}", f"MA{cfg.ma_mid}", f"MA{cfg.ma_long}",
        f"ATR{cfg.atr_period}", f"RSI{cfg.rsi_period}",
        "MACD", "MACD_SIGNAL", "MACD_HIST",
        "BB_Z", f"SLOPE_{cfg.slope_short}", f"SLOPE_{cfg.slope_mid}",
        "VWAP_GAP_PCT", "RET_1", "RET_5", "RET_20",
        "GCROSS", "DCROSS"
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


__all__ = [
    "FeatureConfig",
    "make_features",
    "vwap_series",
    "rsi_series",
    "macd_series",
]