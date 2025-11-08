# aiapp/services/fetch_price.py
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
安定版 価格取得サービス
- 優先: ローカルキャッシュ (Parquet/CSV)
- 失敗時: yfinance で再取得（cookie/crumb 処理済み・401回避）
- キャッシュは銘柄ごとに日足を保存、TTL は 1 日
- 取得失敗時は「最後に成功したキャッシュ」を返す（ダミーは返さない）
- 返却: pandas.DataFrame(index=DatetimeIndex[tz-naive], cols=open/high/low/close/volume), nbars で末尾トリム
"""

import os
import io
import time
import math
import json
import gzip
import shutil
import typing as T
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None  # 後で検出してエラーメッセージ

from django.conf import settings

# --- 定数/パス ---------------------------------------------------------------

MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
CACHE_DIR  = MEDIA_ROOT / "aiapp" / "cache_prices"   # 実データキャッシュ
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# キャッシュ有効期限（秒）— 取引所終値で十分なので 18 時間
CACHE_TTL_SEC = int(os.getenv("AIAPP_PRICE_CACHE_TTL", "64800"))

# 1回の DL 期間（初回は 3 年、以降は 30 日に短縮してマージ）
FULL_YEARS = int(os.getenv("AIAPP_PRICE_YEARS_FULL", "3"))
INCR_DAYS  = int(os.getenv("AIAPP_PRICE_DAYS_INCR", "30"))

# タイムアウト/リトライ（yfinance は内部で requests を使う）
HTTP_TIMEOUT = float(os.getenv("AIAPP_HTTP_TIMEOUT", "8.0"))
HTTP_RETRIES = int(os.getenv("AIAPP_HTTP_RETRIES", "2"))

# デバッグ出力
DEBUG = bool(int(os.getenv("AIAPP_PRICE_DEBUG", "0")))

# --- ユーティリティ ----------------------------------------------------------

def _log(*a):
    if DEBUG:
        print("[fetch_price]", *a, flush=True)

def _ticker_for(code: str) -> str:
    """
    JP: XXXX.T で統一（ETF/REIT も .T で OK）
    """
    code = str(code).strip()
    return f"{code}.T" if not code.endswith(".T") else code

def _cache_path(code: str) -> Path:
    return CACHE_DIR / f"{code}.parquet"

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _is_fresh(p: Path) -> bool:
    try:
        age = _now() - datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        return age.total_seconds() < CACHE_TTL_SEC
    except FileNotFoundError:
        return False

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open","high","low","close","volume"])
    # yfinance: columns が [Open, High, Low, Close, Adj Close, Volume]
    cols = {c.lower(): c for c in df.columns}
    o = df[[cols.get("open"), cols.get("high"), cols.get("low"),
            cols.get("close"), cols.get("volume")]].copy()
    o.columns = ["open","high","low","close","volume"]
    # index を tz-naive DatetimeIndex に
    if not isinstance(o.index, pd.DatetimeIndex):
        o.index = pd.to_datetime(o.index)
    o.index = o.index.tz_localize(None)
    o = o.dropna()
    return o

def _read_cache(code: str) -> pd.DataFrame:
    p = _cache_path(code)
    if p.exists():
        try:
            df = pd.read_parquet(p)
        except Exception:
            # 古い形式(csv.gz)への後方互換
            try:
                df = pd.read_csv(p.with_suffix(".csv"), parse_dates=["Date"])
                df = df.set_index("Date")
            except Exception:
                return pd.DataFrame()
        return _normalize(df)
    return pd.DataFrame()

def _write_cache(code: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    df = _normalize(df)
    _cache_path(code).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_cache_path(code))

def _download_yf(code: str, full: bool) -> pd.DataFrame:
    if yf is None:
        raise RuntimeError("yfinance が未インストールです。pip install yfinance を実行してください。")

    ticker = _ticker_for(code)
    period = f"{FULL_YEARS}y" if full else f"{INCR_DAYS}d"
    _log("yfinance download", ticker, "period=", period)
    # NOTE: yfinanceは内部でUser-Agent等も設定してくれる。timeoutは環境変数で調整。
    df = yf.download(
        tickers=ticker,
        period=period,
        interval="1d",
        progress=False,
        threads=False,
        timeout=HTTP_TIMEOUT,
        auto_adjust=False,
    )
    return _normalize(df)

def _merge(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if old is None or old.empty:
        return new
    if new is None or new.empty:
        return old
    m = pd.concat([old, new]).sort_index()
    m = m[~m.index.duplicated(keep="last")]
    return m

# --- Public API --------------------------------------------------------------

def get_prices(code: str, nbars: int) -> pd.DataFrame:
    """
    実データのみ返す。
    - キャッシュが新鮮 → そのまま nbars 返す
    - 古い/なし → yfinance で取得 → キャッシュ更新 → nbars 返す
    - yfinance が失敗した場合:
        * 古いキャッシュがあればそれを返す
        * それも無ければ空 DataFrame を返す（ダミーは返さない）
    """
    code = str(code).strip()
    cache = _read_cache(code)
    p = _cache_path(code)

    # 新鮮ならキャッシュ優先
    if _is_fresh(p) and not cache.empty:
        return cache.tail(nbars)

    # まず“差分”取得を試み、無ければフル
    merged = cache.copy()
    ok = False
    tries = HTTP_RETRIES + 1
    for i in range(tries):
        try:
            incr = _download_yf(code, full=False if not cache.empty else True)
            if not incr.empty:
                merged = _merge(cache, incr)
                _write_cache(code, merged)
                ok = True
                break
            # 差分が空ならフルで保険
            incr = _download_yf(code, full=True)
            if not incr.empty:
                merged = _merge(cache, incr)
                _write_cache(code, merged)
                ok = True
                break
        except Exception as e:
            _log("retry", i+1, "err=", repr(e))
            time.sleep(0.8 * (i+1))

    if ok and not merged.empty:
        return merged.tail(nbars)

    # 取得に失敗：最後のキャッシュで妥協（無ければ空）
    if not cache.empty:
        return cache.tail(nbars)
    return pd.DataFrame(columns=["open","high","low","close","volume"])