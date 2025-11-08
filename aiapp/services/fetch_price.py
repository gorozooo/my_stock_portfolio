# -*- coding: utf-8 -*-
"""
aiapp.services.fetch_price

・HTTPに確実なタイムアウト/リトライを設定（ハング防止）
・価格を /media/aiapp/cache/prices/{code}.parquet にキャッシュ
・キャッシュが新鮮（既定2日以内）ならネットに出ず即返答
・通信失敗時はキャッシュへフェイルオーバー（なければ空DF）

get_prices(code: str, nbars: int) -> pandas.DataFrame
  - 必要本数 nbars だけ末尾から返す（日足時系列 / index=DatetimeIndex)
  - 列: open, high, low, close, volume
"""

from __future__ import annotations

import io
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings

JST = timezone(timedelta(hours=9))

# ---- tunables (env) ----------------------------------------------------------
HTTP_TIMEOUT = float(os.environ.get("AIAPP_HTTP_TIMEOUT", "1.6"))  # 秒（connect+read）
HTTP_RETRIES = int(os.environ.get("AIAPP_HTTP_RETRIES", "1"))
CACHE_DAYS   = int(os.environ.get("AIAPP_PRICE_CACHE_DAYS", "2"))

# 既存の価格APIを使っている場合はそのまま base を指定。
# 未指定なら Yahoo CSV ダウンロードにフォールバック（.T を付けて取得）
PRICE_BASE   = os.environ.get("AIAPP_PRICE_BASE", "").strip()

MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
CACHE_DIR  = MEDIA_ROOT / "aiapp" / "cache" / "prices"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---- HTTP session with retry/timeout ----------------------------------------
def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=HTTP_RETRIES,
        connect=HTTP_RETRIES,
        read=HTTP_RETRIES,
        backoff_factor=0.2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=32)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({"User-Agent": "aiapp/price-fetcher"})
    return s

_SESS = _session()


# ---- helpers -----------------------------------------------------------------
def _cache_path(code: str) -> Path:
    return CACHE_DIR / f"{code}.parquet"

def _load_cache(code: str) -> Optional[pd.DataFrame]:
    p = _cache_path(code)
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        return df
    except Exception:
        return None

def _is_fresh(df: pd.DataFrame) -> bool:
    try:
        last = df.index.max()
        if pd.isna(last):
            return False
        last_date = pd.Timestamp(last).tz_localize(None).date()
        return last_date >= (date.today() - timedelta(days=CACHE_DAYS))
    except Exception:
        return False

def _save_cache(code: str, df: pd.DataFrame) -> None:
    try:
        p = _cache_path(code)
        df.to_parquet(p, index=True)
    except Exception:
        pass


# ---- source: Yahoo CSV fallback (日足) ---------------------------------------
def _fetch_yahoo_daily(code: str, nbars: int) -> Optional[pd.DataFrame]:
    """
    例示: YahooのCSVダウンロードAPIで日足を取得
    - 証券コードは {code}.T を想定
    """
    # 直近 nbars の余裕を持って 540日分取りにいく
    days = max(540, nbars * 3)
    period2 = int(time.time())
    period1 = period2 - days * 86400

    sym = f"{code}.T" if not code.endswith(".T") else code
    url = (
        "https://query1.finance.yahoo.com/v7/finance/download/"
        f"{sym}?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    r = _SESS.get(url, timeout=HTTP_TIMEOUT)
    if r.status_code != 200 or not r.content:
        return None

    try:
        df = pd.read_csv(io.BytesIO(r.content))
        if df.empty:
            return None
        df = df.rename(columns={
            "Date": "date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Adj Close": "adj_close", "Volume": "volume"
        })
        df = df.dropna(subset=["date"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df[["open", "high", "low", "close", "volume"]]
        # 数値化
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"])
        return df
    except Exception:
        return None


# ---- source: custom base (既存API) -------------------------------------------
def _fetch_from_base(code: str, nbars: int) -> Optional[pd.DataFrame]:
    """
    既存の自前API/エンドポイントがある場合の取得器。
    PRICE_BASE が空なら None を返し、Yahoo fallback が使われる。
    期待形式: CSV or JSON を受け取り、open/high/low/close/volume に整形。
    """
    if not PRICE_BASE:
        return None
    # 例: GET {PRICE_BASE}/daily?code=XXXX&limit=nbars
    try:
        url = f"{PRICE_BASE.rstrip('/')}/daily?code={code}&limit={max(60, nbars*3)}"
        r = _SESS.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200 or not r.content:
            return None

        # JSON想定（適宜プロジェクトの実仕様に合わせて調整）
        if r.headers.get("Content-Type", "").lower().startswith("application/json"):
            js = r.json()
            df = pd.DataFrame(js)
        else:
            df = pd.read_csv(io.BytesIO(r.content))

        # 列名マップを柔軟に吸収
        cols = {c.lower(): c for c in df.columns}
        def pick(*ks):
            for k in ks:
                for lc, orig in cols.items():
                    if k in lc:
                        return orig
            return None
        dc = pick("date")
        oc = pick("open"); hc = pick("high"); lc = pick("low"); cc = pick("close")
        vc = pick("volume")

        needs = [dc, oc, hc, lc, cc]
        if any(x is None for x in needs):
            return None
        df = df.rename(columns={dc:"date", oc:"open", hc:"high", lc:"low", cc:"close", (vc or "volume"):"volume"})
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"])
        return df
    except Exception:
        return None


# ---- public API --------------------------------------------------------------
def get_prices(code: str, nbars: int) -> pd.DataFrame:
    """
    可能な限り速く・確実に nbars 本返す。
    - 新鮮なキャッシュがあればそれを利用
    - ネット取得は確実にタイムアウト設定
    - 失敗時はキャッシュへフェイルオーバー（最終的に空DFもあり得る）
    """
    nbars = int(max(1, nbars))

    # 1) キャッシュ
    cached = _load_cache(code)
    if cached is not None and _is_fresh(cached):
        df = cached.tail(nbars).copy()
        df.index = pd.to_datetime(df.index)
        return df

    # 2) ネット取得（まずは既存BASE、だめならYahoo）
    df_remote: Optional[pd.DataFrame] = None

    try:
        df_remote = _fetch_from_base(code, nbars)
    except Exception:
        df_remote = None

    if df_remote is None:
        try:
            df_remote = _fetch_yahoo_daily(code, nbars)
        except Exception:
            df_remote = None

    # 3) 保存 & 返却 or フェイルオーバー
    if df_remote is not None and not df_remote.empty:
        _save_cache(code, df_remote)  # 生データを保存（将来nbars違いでも活用できる）
        return df_remote.tail(nbars).copy()

    # 4) 失敗時は古いキャッシュで妥協
    if cached is not None and not cached.empty:
        return cached.tail(nbars).copy()

    # 5) それでも無理なら空
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])