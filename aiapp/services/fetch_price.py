# -*- coding: utf-8 -*-
"""
aiapp.services.fetch_price
- yfinanceから堅牢に日足OHLCVを取得・正規化
- 列名ゆらぎ（MultiIndex/Adj Closeのみ等）を吸収し、必ず
  index=DatetimeIndex, columns=["Open","High","Low","Close","Volume"] で返す
- nbars指定で末尾N本をスライス
"""

from __future__ import annotations
import datetime as dt
from typing import Optional
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None  # オフライン環境対策


# =========================================================
# Snapshot 保存先（prices_snapshot_nightly が import する）
# =========================================================
SNAP_DIR = Path("media/aiapp/prices")
SNAP_DIR.mkdir(parents=True, exist_ok=True)


# ---------- 内部ヘルパ ----------

def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        flat = []
        for col in df.columns:
            parts = [str(x) for x in col if x is not None and str(x) != ""]
            flat.append("_".join(parts))
        df = df.copy()
        df.columns = flat
    return df

def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    df を ["Open","High","Low","Close","Volume"] に正規化
    - 'adj close','adj_close','adjclose','price','last','last_close' は Close に寄せる
    - 'vol','v' は Volume
    - 数値化、NaT/重複日除去、昇順
    """
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame(columns=["Open","High","Low","Close","Volume"])

    df = _flatten_columns(df)

    low2orig = {str(c).strip().lower(): c for c in df.columns}

    def pick(*aliases: str) -> Optional[str]:
        for a in aliases:
            if a in low2orig:
                return low2orig[a]
            # 'close_XXXX' のようなフラット列も拾う
            cand = [k for k in low2orig.keys() if k.startswith(a + "_")]
            if cand:
                return low2orig[cand[0]]
        return None

    col_o = pick("open", "o")
    col_h = pick("high", "h")
    col_l = pick("low", "l")
    col_c = pick("close", "c", "adj close", "adj_close", "adjclose", "price", "last", "last_close")
    col_v = pick("volume", "vol", "v")

    out = pd.DataFrame(index=df.index.copy())
    out["Open"]   = pd.to_numeric(df[col_o], errors="coerce") if col_o else np.nan
    out["High"]   = pd.to_numeric(df[col_h], errors="coerce") if col_h else np.nan
    out["Low"]    = pd.to_numeric(df[col_l], errors="coerce") if col_l else np.nan
    # Close 無ければ Adj Close/price等が補充済み。最悪でも NaN 列。
    out["Close"]  = pd.to_numeric(df[col_c], errors="coerce") if col_c else np.nan
    out["Volume"] = pd.to_numeric(df[col_v], errors="coerce") if col_v else 0.0

    # index 正規化
    idx = pd.to_datetime(out.index, errors="coerce")
    mask = ~idx.isna()
    out = out.loc[mask]
    out.index = idx[mask]
    out = out[~out.index.duplicated(keep="last")]
    out = out.sort_index()

    # 最低限の補完（終値→始値、H/L）
    out["Close"]  = out["Close"].ffill()
    out["Open"]   = out["Open"].fillna(out["Close"])
    out["High"]   = out["High"].fillna(out[["Open","Close"]].max(axis=1))
    out["Low"]    = out["Low"].fillna(out[["Open","Close"]].min(axis=1))
    out["Volume"] = out["Volume"].fillna(0)

    # すべてNaNなら空DFに
    if out[["Open","High","Low","Close"]].isna().all().all():
        return pd.DataFrame(columns=["Open","High","Low","Close","Volume"])

    return out[["Open","High","Low","Close","Volume"]]

def _to_symbol(code: str) -> str:
    c = str(code).strip()
    if c.endswith(".T"):
        return c
    # 日本株コード（数字のみ）想定は .T を付与
    if c.isdigit():
        return c + ".T"
    return c  # それ以外はそのまま

# ---------- 公開API ----------

def get_prices(code: str, nbars: Optional[int] = None, period: str = "3y") -> pd.DataFrame:
    """
    yfinanceから code の日足を取得し正規化して返す。
    - nbars 指定があれば末尾からスライス
    - 空/欠損に対しても DataFrame を返し、呼び出し側が安全に扱えるようにする
    """
    # yfinance が無い環境でも落とさない
    if yf is None:
        return pd.DataFrame(columns=["Open","High","Low","Close","Volume"])

    sym = _to_symbol(code)
    try:
        # auto_adjustを明示（将来デフォ変更に影響されない）
        df = yf.download(
            sym,
            period=period,
            interval="1d",
            progress=False,
            threads=False,
            auto_adjust=False,
        )
    except Exception:
        # ネットワーク・銘柄エラー等
        return pd.DataFrame(columns=["Open","High","Low","Close","Volume"])

    # yfinanceは単一銘柄でも MultiIndex になる場合があるので normalize
    df = _normalize_ohlcv(df)

    if nbars is not None and nbars > 0 and len(df) > nbars:
        df = df.iloc[-nbars:].copy()

    return df