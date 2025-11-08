# -*- coding: utf-8 -*-
"""
価格取得ユーティリティ（JP株フル対応）
- マルチソース: Yahoo Finance / Stooq（順次フォールバック）
- サフィックス自動リトライ: 7203 → 7203.T / 7203.jp / 7203
- 列名を小文字統一: open, high, low, close, volume
- nbars=… 本だけ末尾から返す（足りなければあるだけ）
- シンプルなディスクキャッシュ（MEDIA_ROOT/aiapp/cache/prices/{code}.csv）

環境変数（任意）
- AIAPP_HTTP_TIMEOUT  : 1リクエストのタイムアウト秒 (float, default 2.5)
- AIAPP_HTTP_RETRIES  : HTTPリトライ回数 (int, default 1)
- AIAPP_PRICE_CACHE_TTL_DAYS : キャッシュ有効日数 (int, default 1)
"""
from __future__ import annotations

import os
import io
import time
import math
import json
import gzip
import shutil
import random
import zipfile
import datetime as dt
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests
from django.conf import settings

# -------------------- 設定 --------------------
MEDIA = Path(getattr(settings, "MEDIA_ROOT", "media"))
CACHE_DIR = MEDIA / "aiapp" / "cache" / "prices"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = float(os.getenv("AIAPP_HTTP_TIMEOUT", "2.5"))
HTTP_RETRIES = int(os.getenv("AIAPP_HTTP_RETRIES", "1"))
CACHE_TTL_DAYS = int(os.getenv("AIAPP_PRICE_CACHE_TTL_DAYS", "1"))

UA = {"User-Agent": "Mozilla/5.0 (compatible; aiapp-fetch/1.0)"}

# -------------------- 共通ユーティリティ --------------------
def _now_jst_date() -> dt.date:
    return (dt.datetime.utcnow() + dt.timedelta(hours=9)).date()

def _safe_read_csv(content: bytes) -> pd.DataFrame:
    # CSV → DataFrame。列名を小文字化し、日付列をindexに
    bio = io.BytesIO(content)
    try:
        df = pd.read_csv(bio)
    except Exception:
        # 文字コードを変えたり微調整
        bio.seek(0)
        df = pd.read_csv(bio, encoding="cp932")
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Yahoo用（date列）/ Stooq用（date列）を想定
    date_col = None
    for cand in ("date",):
        if cand in df.columns:
            date_col = cand
            break
    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])
        df = df.set_index(date_col).sort_index()

    # 列名を最終標準化
    rename_map = {
        "open": "open", "high": "high", "low": "low",
        "close": "close", "adj close": "close", "adjclose": "close",
        "volume": "volume", "vol": "volume",
    }
    std_cols = {}
    for c in df.columns:
        std_cols[c] = rename_map.get(c, c)
    df = df.rename(columns=std_cols)

    # 必須が無ければ空に
    need = {"open", "high", "low", "close", "volume"}
    if not need.intersection(set(df.columns)):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    # 欠損は落とす/補完（volumeが無い市場もあるためfillna(0)）
    if "volume" in df.columns:
        df["volume"] = df["volume"].fillna(0)

    # 余分は捨てて最低限
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep]
    return df

def _http_get(url: str) -> Optional[bytes]:
    last_err = None
    for i in range(max(1, HTTP_RETRIES + 1)):
        try:
            r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT)
            if r.status_code == 200 and r.content:
                return r.content
        except Exception as e:
            last_err = e
        time.sleep(0.15)
    return None

# -------------------- 各ソース --------------------
def _yf_symbol_variants(code: str) -> List[str]:
    # 例: 7203 → 7203.T（Yahoo）、ETF等でもまず .T を試す
    return [f"{code}.T", f"{code}.JP", code]

def _stooq_symbol_variants(code: str) -> List[str]:
    # 例: 7203 → 7203.jp が定番
    return [f"{code}.jp", f"{code}.t", code]

def _fetch_yahoo_daily(code: str, nbars: int) -> pd.DataFrame:
    # 過去 ~3年分くらい（nbarsに十分な余裕を持たせる）
    period2 = int(time.time())
    period1 = period2 - 60*60*24*365*5
    for sym in _yf_symbol_variants(code):
        url = (
            "https://query1.finance.yahoo.com/v7/finance/download/"
            f"{sym}?period1={period1}&period2={period2}"
            "&interval=1d&events=history&includeAdjustedClose=true"
        )
        content = _http_get(url)
        if not content:
            continue
        df = _safe_read_csv(content)
        if len(df) > 0:
            return df
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

def _fetch_stooq_daily(code: str, nbars: int) -> pd.DataFrame:
    # Stooq: https://stooq.com/q/d/l/?s=7203.jp&i=d
    for sym in _stooq_symbol_variants(code):
        url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
        content = _http_get(url)
        if not content:
            continue
        df = _safe_read_csv(content)
        if len(df) > 0:
            return df
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

# -------------------- キャッシュ --------------------
def _cache_path(code: str) -> Path:
    return CACHE_DIR / f"{code}.csv"

def _read_cache(code: str) -> Optional[pd.DataFrame]:
    p = _cache_path(code)
    if not p.exists():
        return None
    try:
        mtime = dt.datetime.fromtimestamp(p.stat().st_mtime)
        if (_now_jst_date() - mtime.date()).days > CACHE_TTL_DAYS:
            return None
        df = pd.read_csv(p, parse_dates=[0], index_col=0)
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception:
        return None

def _write_cache(code: str, df: pd.DataFrame) -> None:
    try:
        df.to_csv(_cache_path(code))
    except Exception:
        pass

# -------------------- 公開API --------------------
def get_prices(code: str, nbars: int = 180) -> pd.DataFrame:
    """
    code: '7203' のような4桁（ETF/REIT含む）
    nbars: 末尾から最大この本数を返す
    返り値: index=Datetime, columns=['open','high','low','close','volume']
    空の場合は len(df)==0
    """
    # 1) キャッシュ
    cached = _read_cache(code)
    if cached is not None and len(cached) >= min(20, nbars//2):
        return cached.tail(nbars).copy()

    # 2) Yahoo → 3) Stooq の順に試す
    df = _fetch_yahoo_daily(code, nbars)
    if len(df) == 0:
        df = _fetch_stooq_daily(code, nbars)

    # 4) 結果
    if len(df) > 0:
        df = df.sort_index()
        df = df.tail(nbars)
        _write_cache(code, df)
        return df

    # 5) だめなら空DataFrame
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])