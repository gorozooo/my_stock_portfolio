# -*- coding: utf-8 -*-
"""
価格取得（日本株フル対応 + オフライン可）
優先順:
  0) ローカル種CSV (MEDIA_ROOT/aiapp/seed_prices/{code}.csv)
  1) Yahoo Finance (7203 → 7203.T / 7203.JP / 7203)
  2) Stooq        (7203 → 7203.jp / 7203.t / 7203)

共通仕様:
- 返却: index=Datetime, columns=['open','high','low','close','volume']
- 列名は小文字に正規化
- CSVキャッシュ: MEDIA_ROOT/aiapp/cache/prices/{code}.csv
- nbars 本だけ末尾から返す

環境変数:
- AIAPP_HTTP_TIMEOUT             … 1リクエストのタイムアウト秒 (float, default 2.5)
- AIAPP_HTTP_RETRIES             … リトライ回数 (int, default 1)
- AIAPP_PRICE_CACHE_TTL_DAYS     … キャッシュ有効日数 (int, default 1)
- AIAPP_PRICE_DEBUG              … '1'で取得URL/結果をログ出力
- AIAPP_PRICE_SEED_DIR           … 既定以外の種CSVディレクトリを指定可
"""
from __future__ import annotations

import os
import io
import time
import datetime as dt
from pathlib import Path
from typing import List, Optional

import pandas as pd
import requests
from django.conf import settings

# -------------------- パス/環境 --------------------
MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
CACHE_DIR = MEDIA_ROOT / "aiapp" / "cache" / "prices"
SEED_DIR = Path(os.getenv("AIAPP_PRICE_SEED_DIR") or (MEDIA_ROOT / "aiapp" / "seed_prices"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SEED_DIR.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = float(os.getenv("AIAPP_HTTP_TIMEOUT", "2.5"))
HTTP_RETRIES = int(os.getenv("AIAPP_HTTP_RETRIES", "1"))
CACHE_TTL_DAYS = int(os.getenv("AIAPP_PRICE_CACHE_TTL_DAYS", "1"))
DEBUG = os.getenv("AIAPP_PRICE_DEBUG", "0") == "1"

UA = {"User-Agent": "Mozilla/5.0 (compatible; aiapp-fetch/1.0)"}

def _log(*args):
    if DEBUG:
        print("[fetch_price]", *args, flush=True)

# -------------------- ユーティリティ --------------------
def _safe_read_csv(content: bytes) -> pd.DataFrame:
    bio = io.BytesIO(content)
    try:
        df = pd.read_csv(bio)
    except Exception:
        bio.seek(0)
        df = pd.read_csv(bio, encoding="cp932")
    df.columns = [str(c).strip().lower() for c in df.columns]

    date_col = None
    for cand in ("date",):
        if cand in df.columns:
            date_col = cand
            break
    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", utc=False)
        df = df.dropna(subset=[date_col]).set_index(date_col).sort_index()

    rename_map = {
        "open": "open", "high": "high", "low": "low",
        "close": "close", "adj close": "close", "adjclose": "close",
        "volume": "volume", "vol": "volume",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    if not keep:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if "volume" in df.columns:
        df["volume"] = df["volume"].fillna(0)
    return df[keep]

def _http_get(url: str) -> Optional[bytes]:
    last_err = None
    for _ in range(max(1, HTTP_RETRIES + 1)):
        try:
            _log("GET", url)
            r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT)
            if r.status_code == 200 and r.content:
                _log("OK", len(r.content), "bytes")
                return r.content
            _log("HTTP", r.status_code)
        except Exception as e:
            last_err = e
            _log("ERR", repr(e))
        time.sleep(0.15)
    return None

# -------------------- ソース別取得 --------------------
def _yf_symbol_variants(code: str) -> List[str]:
    return [f"{code}.T", f"{code}.JP", code]

def _stooq_symbol_variants(code: str) -> List[str]:
    return [f"{code}.jp", f"{code}.t", code]

def _fetch_yahoo_daily(code: str) -> pd.DataFrame:
    period2 = int(time.time())
    period1 = period2 - 60 * 60 * 24 * 365 * 5
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

def _fetch_stooq_daily(code: str) -> pd.DataFrame:
    for sym in _stooq_symbol_variants(code):
        url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
        content = _http_get(url)
        if not content:
            continue
        df = _safe_read_csv(content)
        if len(df) > 0:
            return df
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

# -------------------- キャッシュ/シード --------------------
def _cache_path(code: str) -> Path:
    return CACHE_DIR / f"{code}.csv"

def _read_cache(code: str) -> Optional[pd.DataFrame]:
    p = _cache_path(code)
    if not p.exists():
        return None
    try:
        mtime = dt.datetime.fromtimestamp(p.stat().st_mtime)
        if (dt.date.today() - mtime.date()).days > CACHE_TTL_DAYS:
            return None
        df = pd.read_csv(p, parse_dates=[0], index_col=0)
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        _log("cache read err", code, e)
        return None

def _write_cache(code: str, df: pd.DataFrame) -> None:
    try:
        df.to_csv(_cache_path(code))
    except Exception as e:
        _log("cache write err", code, e)

def _read_seed(code: str) -> Optional[pd.DataFrame]:
    p = SEED_DIR / f"{code}.csv"
    if not p.exists():
        return None
    try:
        _log("read seed", str(p))
        df = pd.read_csv(p, parse_dates=[0], index_col=0)
        df.columns = [c.lower() for c in df.columns]
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep].sort_index()
        return df
    except Exception as e:
        _log("seed read err", code, e)
        return None

# -------------------- 公開関数 --------------------
def get_prices(code: str, nbars: int = 180) -> pd.DataFrame:
    """
    返り値が空なら外部到達不可の可能性大。DEBUG=1で試行ログを確認してください。
    """
    # 0) シード（ローカル）最優先
    df = _read_seed(code)
    if df is not None and len(df) > 0:
        return df.tail(nbars).copy()

    # 1) キャッシュ
    cached = _read_cache(code)
    if cached is not None and len(cached) >= min(20, nbars // 2):
        return cached.tail(nbars).copy()

    # 2) Yahoo → 3) Stooq
    df = _fetch_yahoo_daily(code)
    if len(df) == 0:
        df = _fetch_stooq_daily(code)

    if len(df) > 0:
        df = df.sort_index().tail(nbars)
        _write_cache(code, df)
        return df

    # どれもダメ
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])