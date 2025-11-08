# -*- coding: utf-8 -*-
"""
aiapp.services.fetch_price
- 価格取得のハイブリッド実装（seed → snapshot → stooq → yfinance）
- 短時間(FAST/LITE)と夜間(HEAVY/NIGHT)で動作を環境変数で制御
- 依存最小化のため、yfinance は任意（インポート失敗なら自動スキップ）
- 外部が不安定でも seed / snapshot があれば UI は止まらない

環境変数:
  AIAPP_PRICE_SOURCES   = "seed,snapshot,stooq,yfinance" など（優先順）
  AIAPP_HTTP_TIMEOUT    = float (例 1.8, 6.5)
  AIAPP_HTTP_RETRIES    = int   (例 0, 1, 2)
  AIAPP_SNAPSHOT_DIR    = "media/aiapp/snapshots"
  AIAPP_PRICE_DEBUG     = "1" で詳細ログ

スナップショット仕様:
  - ディレクトリ: {SNAPSHOT_DIR}/{YYYYMMDD}/{code}.csv
  - CSV列: Date,open,high,low,close,volume
"""

from __future__ import annotations

import io
import os
import time
import math
import json
import gzip
import random
import logging
import datetime as dt
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
import requests
from django.conf import settings

log = logging.getLogger(__name__)

# ---------- 環境 ----------
MEDIA_ROOT = getattr(settings, "MEDIA_ROOT", "media")
SEED_DIR   = os.path.join(MEDIA_ROOT, "aiapp", "seed_prices")
SNAP_DIR   = os.getenv("AIAPP_SNAPSHOT_DIR", os.path.join(MEDIA_ROOT, "aiapp", "snapshots"))
DEBUG      = os.getenv("AIAPP_PRICE_DEBUG", "0") == "1"

HTTP_TIMEOUT = float(os.getenv("AIAPP_HTTP_TIMEOUT", "2.0"))
HTTP_RETRIES = int(os.getenv("AIAPP_HTTP_RETRIES", "0"))

SOURCES = [s.strip().lower() for s in os.getenv(
    "AIAPP_PRICE_SOURCES",
    "seed,snapshot,stooq,yfinance"
).split(",") if s.strip()]

# yfinance は任意依存
try:
    import yfinance as yf  # type: ignore
except Exception:
    yf = None


def _dlog(*a):
    if DEBUG:
        print("[fetch_price]", *a)


# ---------- 共通ユーティリティ ----------
COLS = ["open", "high", "low", "close", "volume"]

def _norm_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)
    df = df.copy()
    # 列名ゆらぎの吸収
    mapping = {}
    low = {str(c).lower(): c for c in df.columns}
    for want, cands in {
        "open":   ["open", "o"],
        "high":   ["high", "h"],
        "low":    ["low", "l"],
        "close":  ["close", "adj close", "c", "adj_close", "close*"],
        "volume": ["volume", "v", "vol"],
    }.items():
        for k in cands:
            if k in low:
                mapping[low[k]] = want
                break
    df = df.rename(columns=mapping)
    for k in COLS:
        if k not in df.columns:
            df[k] = pd.NA
    # index を DatetimeIndex 化
    if "Date" in df.columns:
        df = df.set_index(pd.to_datetime(df["Date"], errors="coerce"))
        df = df.drop(columns=["Date"], errors="ignore")
    elif not isinstance(df.index, pd.DatetimeIndex):
        # 最後の手段
        if "date" in df.columns:
            df = df.set_index(pd.to_datetime(df["date"], errors="coerce"))
            df = df.drop(columns=["date"], errors="ignore")
        else:
            df.index = pd.to_datetime(df.index, errors="coerce")

    df = df.sort_index()
    df = df[COLS]
    df = df.dropna(how="all")
    return df


def _limit_nbars(df: pd.DataFrame, nbars: int) -> pd.DataFrame:
    if nbars and len(df) > nbars:
        return df.iloc[-nbars:]
    return df


# ---------- seed ----------
def _try_seed(code: str, nbars: int) -> pd.DataFrame:
    path = os.path.join(SEED_DIR, f"{code}.csv")
    if os.path.exists(path):
        _dlog("read seed", path)
        try:
            df = pd.read_csv(path)
            return _limit_nbars(_norm_df(df), nbars)
        except Exception as e:
            _dlog("seed error:", e)
    return pd.DataFrame(columns=COLS)


# ---------- snapshot ----------
def _today_dir() -> str:
    return os.path.join(SNAP_DIR, dt.date.today().strftime("%Y%m%d"))

def _yesterday_dir() -> str:
    return os.path.join(SNAP_DIR, (dt.date.today() - dt.timedelta(days=1)).strftime("%Y%m%d"))

def _try_snapshot(code: str, nbars: int) -> pd.DataFrame:
    # 当日 → 前日 の順
    for base in (_today_dir(), _yesterday_dir()):
        path = os.path.join(base, f"{code}.csv")
        if os.path.exists(path):
            _dlog("read snapshot", path)
            try:
                df = pd.read_csv(path)
                return _limit_nbars(_norm_df(df), nbars)
            except Exception as e:
                _dlog("snapshot error:", e)
    return pd.DataFrame(columns=COLS)


# ---------- stooq ----------
def _http_get(url: str, timeout: float) -> Optional[requests.Response]:
    last = None
    for i in range(max(1, HTTP_RETRIES + 1)):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = str(e)
        time.sleep(0.05 * (i + 1))
    _dlog("stooq error:", last)
    return None

def _try_stooq(code: str, nbars: int, timeout: float) -> pd.DataFrame:
    # JP銘柄は *.jp or *.t など複数候補がある
    cands = [f"{code}.jp", f"{code}.t", f"{code}"]
    for sym in cands:
        for scheme in ("https", "http"):
            url = f"{scheme}://stooq.com/q/d/l/?s={sym}&i=d"
            _dlog("stooq GET", url)
            resp = _http_get(url, timeout)
            if not resp:
                continue
            try:
                df = pd.read_csv(io.StringIO(resp.text))
                if not df.empty:
                    return _limit_nbars(_norm_df(df), nbars)
            except Exception as e:
                _dlog("stooq parse error:", e)
    return pd.DataFrame(columns=COLS)


# ---------- yfinance ----------
def _try_yfinance(code: str, nbars: int) -> pd.DataFrame:
    if yf is None:
        return pd.DataFrame(columns=COLS)
    # 候補（市場サフィックス）
    cands = [f"{code}.T", f"{code}.JP", f"{code}"]
    for sym in cands:
        try:
            _dlog("yfinance download", sym, "period=", "3y")
            df = yf.download(sym, period="3y", interval="1d", progress=False, threads=False)
            if df is None or df.empty:
                continue
            df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
            df = df[["open", "high", "low", "close", "volume"]]
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            return _limit_nbars(df, nbars)
        except Exception as e:
            _dlog("yfinance error:", e)
    return pd.DataFrame(columns=COLS)


# ---------- public ----------
def get_prices(code: str, nbars: int) -> pd.DataFrame:
    """
    優先順位に従って取得し、nbars本に切り詰めた DataFrame を返す。
    失敗時は空DF（列は open/high/low/close/volume）を返す。
    """
    code = str(code).strip()
    for src in SOURCES:
        if src == "seed":
            df = _try_seed(code, nbars)
        elif src == "snapshot":
            df = _try_snapshot(code, nbars)
        elif src == "stooq":
            df = _try_stooq(code, nbars, HTTP_TIMEOUT)
        elif src == "yfinance":
            df = _try_yfinance(code, nbars)
        else:
            continue
        if not df.empty:
            return df
    return pd.DataFrame(columns=COLS)