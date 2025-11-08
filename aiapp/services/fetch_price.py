# -*- coding: utf-8 -*-
"""
aiapp.services.fetch_price
- 強化版実装：yfinance をメイン、stooq をセカンド、最後にローカル seed CSV をフォールバック。
- ネットワーク環境が厳しいVPSでも通るように、requests.Session に
  ・堅めの User-Agent
  ・リトライ(HTTP/コネクション)
  ・タイムアウト
  を設定して yfinance の session に注入。
- 取得結果はメモリキャッシュ + ディスクキャッシュ（任意）に保存し、再呼び出しを高速化。

ENV (任意):
  AIAPP_PRICE_DEBUG=1         デバッグログ
  AIAPP_HTTP_TIMEOUT=5        タイムアウト秒
  AIAPP_HTTP_RETRIES=2        リトライ回数
  AIAPP_BUILD_WORKERS=8       並列数（使う側の管理コマンドで参照想定）

ローカルシード:
  media/aiapp/seed_prices/{code}.csv  (Date,open,high,low,close,volume)
"""

from __future__ import annotations

import io
import os
import time
import json
import math
import typing as T
import datetime as dt
from pathlib import Path

import pandas as pd

# ---- 設定系 ----
DEBUG = os.getenv("AIAPP_PRICE_DEBUG", "0") == "1"
HTTP_TIMEOUT = float(os.getenv("AIAPP_HTTP_TIMEOUT", "6.5"))
HTTP_RETRIES = int(os.getenv("AIAPP_HTTP_RETRIES", "2"))

# Django settings から MEDIA_ROOT を読めるときは使う（shell からでもOK）
try:
    from django.conf import settings
    MEDIA_ROOT = getattr(settings, "MEDIA_ROOT", "media")
except Exception:
    MEDIA_ROOT = "media"

SEED_DIR = Path(MEDIA_ROOT) / "aiapp" / "seed_prices"
CACHE_DIR = Path(MEDIA_ROOT) / "aiapp" / "price_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 短いメモリキャッシュ（プロセス内）
_mem_cache: dict[tuple[str, int], pd.DataFrame] = {}


def _log(*a: T.Any) -> None:
    if DEBUG:
        print("[fetch_price]", *a)


def _mk_session():
    """
    yfinance のダウンロードで使う requests.Session を自前構築。
    - User-Agent を固定
    - リトライ（コネクション/HTTP両方）
    """
    import requests
    from urllib3.util.retry import Retry
    from requests.adapters import HTTPAdapter

    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    })
    retry = Retry(
        total=HTTP_RETRIES,
        connect=HTTP_RETRIES,
        read=HTTP_RETRIES,
        backoff_factor=0.6,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _ticker_variants(code: str) -> list[str]:
    """
    証券コードから考えられる yfinance/stooq のティッカー候補を列挙。
    例: '7203' -> ['7203.T', '7203.JP', '7203'] など
    """
    c = code.strip()
    out: list[str] = []
    # yfinance日本株の一般パターン
    out.append(f"{c}.T")
    out.append(f"{c}.JP")
    # 生コード（ETFなどで通るケース）
    out.append(c)
    return out


def _load_seed_csv(code: str) -> pd.DataFrame | None:
    p = SEED_DIR / f"{code}.csv"
    if not p.exists():
        return None
    try:
        _log("read seed", str(p))
        df = pd.read_csv(p)
        # 正規化
        colmap = {k.lower(): k for k in df.columns}
        def pick(*keys):
            for k in keys:
                if k in colmap:
                    return colmap[k]
            return None
        date_col = pick("date", "datetime", "time")
        o = pick("open"); h = pick("high"); l = pick("low"); c = pick("close"); v = pick("volume")
        if not (date_col and o and h and l and c and v):
            return None
        df = df[[date_col, o, h, l, c, v]].rename(columns={
            date_col:"Date", o:"open", h:"high", l:"low", c:"close", v:"volume"
        })
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        # floatに
        for k in ["open","high","low","close","volume"]:
            df[k] = pd.to_numeric(df[k], errors="coerce")
        df = df.dropna()
        return df
    except Exception as e:
        _log("seed read error:", e)
        return None


def _save_cache(code: str, df: pd.DataFrame) -> None:
    try:
        if df is None or df.empty:
            return
        p = CACHE_DIR / f"{code}.parquet"
        df.to_parquet(p, index=True)
    except Exception as e:
        _log("cache save error:", e)


def _load_cache(code: str) -> pd.DataFrame | None:
    p = CACHE_DIR / f"{code}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        if isinstance(df.index, pd.DatetimeIndex):
            return df.sort_index()
    except Exception as e:
        _log("cache load error:", e)
    return None


def _fetch_yf(code: str, nbars: int) -> pd.DataFrame | None:
    """
    yfinance で取得（失敗したら None）
    """
    import yfinance as yf

    sess = _mk_session()

    # 期間は nbars 日ではないが、多少多めに取る（移動計算余裕）
    # 例: nbars=180 -> 3y をダウンロードして後で末尾 nbars に絞る
    period = "3y" if nbars >= 180 else ("1y" if nbars >= 60 else "6mo")
    for t in _ticker_variants(code):
        try:
            _log("yfinance download", t, "period=", period)
            df = yf.download(
                t, period=period, interval="1d", auto_adjust=False, progress=False,
                threads=False, session=sess
            )
            # yfinance は列名 'Open','High','Low','Close','Adj Close','Volume'
            if df is None or df.empty:
                continue
            # 標準化
            out = pd.DataFrame({
                "open":  pd.to_numeric(df["Open"], errors="coerce"),
                "high":  pd.to_numeric(df["High"], errors="coerce"),
                "low":   pd.to_numeric(df["Low"], errors="coerce"),
                "close": pd.to_numeric(df["Close"], errors="coerce"),
                "volume":pd.to_numeric(df["Volume"], errors="coerce"),
            })
            out = out.dropna().sort_index()
            if out.empty:
                continue
            _save_cache(code, out)
            return out
        except Exception as e:
            _log("yfinance error:", repr(e))
            # 401やJSONDecodeErrorなどは次のvariantへ
            time.sleep(0.2)
            continue
    return None


def _fetch_stooq(code: str, nbars: int) -> pd.DataFrame | None:
    """
    stooq ミラー（CSVダイレクト）。https が詰まる環境向けに http も順番に試す。
    """
    import requests

    # stooq の記法: 7203 -> 7203.jp / 7203.t / 7203
    cands = [f"{code}.jp", f"{code}.t", f"{code}"]
    bases = [
        "https://stooq.com/q/d/l/?s={sym}&i=d",
        "http://stooq.com/q/d/l/?s={sym}&i=d",
    ]
    sess = _mk_session()
    for sym in cands:
        for base in bases:
            url = base.format(sym=sym)
            try:
                _log("stooq GET", url)
                r = sess.get(url, timeout=HTTP_TIMEOUT)
                if r.status_code != 200 or not r.text or "404 Not Found" in r.text:
                    continue
                df = pd.read_csv(io.StringIO(r.text))
                if df.empty:
                    continue
                # 列名標準化
                colmap = {k.lower(): k for k in df.columns}
                if "date" not in colmap or "close" not in colmap:
                    continue
                dcol = colmap["date"]
                o = colmap.get("open"); h = colmap.get("high"); l = colmap.get("low")
                c = colmap.get("close"); v = colmap.get("volume") or colmap.get("vol")
                if not (o and h and l and c and v):
                    # stooq は最小で OHLCV が出るはずだが、念のため
                    continue
                df = df[[dcol, o, h, l, c, v]].rename(columns={
                    dcol:"Date", o:"open", h:"high", l:"low", c:"close", v:"volume"
                })
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()
                for k in ["open","high","low","close","volume"]:
                    df[k] = pd.to_numeric(df[k], errors="coerce")
                df = df.dropna()
                if df.empty:
                    continue
                _save_cache(code, df)
                return df
            except Exception as e:
                _log("stooq error:", repr(e))
                time.sleep(0.2)
                continue
    return None


def _tail_nbars(df: pd.DataFrame, nbars: int) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if nbars and len(df) > nbars:
        return df.iloc[-nbars:].copy()
    return df.copy()


def get_prices(code: str, nbars: int = 180) -> pd.DataFrame:
    """
    優先順:
      1) メモリキャッシュ
      2) ディスクキャッシュ (parquet)
      3) yfinance（強化セッション）
      4) stooq（https→http）
      5) ローカル seed CSV
    すべてダメでも必ず DataFrame(columns=[open,high,low,close,volume]) を返す。
    """
    key = (code, int(nbars))
    if key in _mem_cache:
        return _tail_nbars(_mem_cache[key], nbars)

    # 2) ディスクキャッシュ
    cached = _load_cache(code)
    if cached is not None and not cached.empty:
        _mem_cache[key] = cached
        return _tail_nbars(cached, nbars)

    # 3) yfinance
    df = _fetch_yf(code, nbars)
    if df is not None and not df.empty:
        _mem_cache[key] = df
        return _tail_nbars(df, nbars)

    # 4) stooq
    df = _fetch_stooq(code, nbars)
    if df is not None and not df.empty:
        _mem_cache[key] = df
        return _tail_nbars(df, nbars)

    # 5) ローカル seed
    seed = _load_seed_csv(code)
    if seed is not None and not seed.empty:
        _mem_cache[key] = seed
        return _tail_nbars(seed, nbars)

    # 何も取れない場合は空スキーマで返す
    _log("all sources failed; return empty df for", code)
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])