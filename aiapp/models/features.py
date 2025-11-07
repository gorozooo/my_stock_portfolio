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

from __future__ import annotations
import numpy as np
import pandas as pd

EPS = 1e-9

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()

def _rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    up = np.where(delta > 0, delta, 0.0)
    down = np.where(delta < 0, -delta, 0.0)
    roll_up = pd.Series(up, index=s.index).rolling(period).mean()
    roll_down = pd.Series(down, index=s.index).rolling(period).mean()
    rs = (roll_up + EPS) / (roll_down + EPS)
    return 100.0 - (100.0 / (1.0 + rs))

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr1 = (high - low).abs()
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def _vwap(df: pd.DataFrame) -> pd.Series:
    # 単純近似：Typical Price * Volume の累積 / Volume累積
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    cum_vol = df["Volume"].replace(0, np.nan).cumsum()
    cum_tpv = (tp * df["Volume"].replace(0, np.nan)).cumsum()
    vwap = cum_tpv / cum_vol
    return vwap.fillna(method="ffill")

def compute_features(df: pd.DataFrame, benchmark_df: pd.DataFrame | None = None) -> dict:
    if df is None or df.empty:
        return {"ok": False, "reason": "empty_df"}

    # 安全に必要列を確認
    need_cols = {"Open","High","Low","Close","Volume"}
    if not need_cols.issubset(set(df.columns)):
        return {"ok": False, "reason": f"missing_cols:{need_cols - set(df.columns)}"}

    # 指標計算
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)

    ema_fast = _ema(close, 10)
    ema_slow = _ema(close, 20)
    ema_slope = ema_fast.diff(3)  # “傾き”の簡易指標

    rsi14 = _rsi(close, 14)
    roc10 = close.pct_change(10) * 100.0

    vol_ma20 = volume.rolling(20).mean()
    vol_ma20_ratio = (volume + EPS) / (vol_ma20 + EPS)

    atr14 = _atr(df, 14)

    # 直近高値更新フラグ（20日）
    rolling_high = df["High"].rolling(20).max()
    breakout_flag = (close > rolling_high.shift(1)).astype(int)

    # VWAP近接率（直近終値がVWAPにどれだけ近いか、%）
    vwap = _vwap(df)
    vwap_proximity = (close - vwap).abs() / (vwap + EPS) * 100.0

    # ベンチマーク相対強度（10日）
    rel_strength_10 = None
    if benchmark_df is not None and not benchmark_df.empty and "Close" in benchmark_df.columns:
        bench = benchmark_df["Close"].astype(float)
        st_ret = close.pct_change(10)
        bm_ret = bench.pct_change(10)
        rel_strength_10 = (st_ret - bm_ret) * 100.0  # 差を%表現

    # 最終行（直近）の値で集約
    feat = {
        "ok": True,
        "ema_fast": float(ema_fast.iloc[-1]) if ema_fast.notna().iloc[-1] else None,
        "ema_slow": float(ema_slow.iloc[-1]) if ema_slow.notna().iloc[-1] else None,
        "ema_slope": float(ema_slope.iloc[-1]) if ema_slope.notna().iloc[-1] else None,
        "rsi14": float(rsi14.iloc[-1]) if rsi14.notna().iloc[-1] else None,
        "roc10": float(roc10.iloc[-1]) if roc10.notna().iloc[-1] else None,
        "vol_ma20_ratio": float(vol_ma20_ratio.iloc[-1]) if vol_ma20_ratio.notna().iloc[-1] else None,
        "atr14": float(atr14.iloc[-1]) if atr14.notna().iloc[-1] else None,
        "breakout_flag": int(breakout_flag.iloc[-1]) if breakout_flag.notna().iloc[-1] else 0,
        "vwap_proximity": float(vwap_proximity.iloc[-1]) if vwap_proximity.notna().iloc[-1] else None,
        "rel_strength_10": float(rel_strength_10.iloc[-1]) if isinstance(rel_strength_10, pd.Series) and rel_strength_10.notna().iloc[-1] else None,
    }
    return feat
