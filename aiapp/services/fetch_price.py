"""
aiapp.services.fetch_price
pandas_datareader の stooq ソースを利用して日足OHLCVを取得。
キャッシュを CSV で保持し、頻繁な外部アクセスを避ける。

記号:
- 日本株: "6758.jp" のように末尾 ".jp"
- ベンチマーク例: "^nikkei" など stooq 準拠の記号（利用可能な範囲で）

設定（任意）:
- AIAPP_MEDIA_ROOT: MEDIA_ROOT を既定使用
- AIAPP_PRICE_DIR: 'aiapp/price'
- AIAPP_UNIVERSE_LIMIT: 1リクエストの最大銘柄数（/picksの安全弁）
"""

from __future__ import annotations
import os
import datetime as dt
import pandas as pd
from pandas_datareader import data as pdr
from django.conf import settings

DEFAULT_MEDIA = getattr(settings, "MEDIA_ROOT", "media")
PRICE_DIR = getattr(settings, "AIAPP_PRICE_DIR", os.path.join("aiapp", "price"))
UNIVERSE_LIMIT = int(getattr(settings, "AIAPP_UNIVERSE_LIMIT", 120))  # 安全値

def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _symbol_for_stooq(code: str) -> str:
    # 日本株の証券コードを stooq 記法に変換（6758 -> 6758.jp）
    code = str(code).strip().lower()
    if code.endswith(".jp"):
        return code
    if code.startswith("^"):  # 指数コードなどはそのまま
        return code
    return f"{code}.jp"

def _cache_path(code: str) -> str:
    media_root = DEFAULT_MEDIA
    out_dir = os.path.join(media_root, PRICE_DIR)
    _ensure_dir(out_dir)
    return os.path.join(out_dir, f"{code}.csv")

def _read_cache(path: str) -> pd.DataFrame | None:
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")
            return df
        except Exception:
            return None
    return None

def _write_cache(path: str, df: pd.DataFrame) -> None:
    df.to_csv(path, index=True)

def get_prices(code: str, lookback_days: int = 180) -> pd.DataFrame:
    """
    指定コードのOHLCV DataFrameを取得（Date index, Open High Low Close Volume）。
    まずキャッシュ、足りなければstooqから取得してキャッシュ更新。
    """
    sym = _symbol_for_stooq(code)
    path = _cache_path(sym.replace("^","_idx_"))  # '^'をファイル名に使わない

    # キャッシュ読込
    cached = _read_cache(path)
    if cached is not None and not cached.empty:
        # 直近データが今日-2日以内ならそのまま（stooqは1日遅延のことあり）
        if cached.index.max() >= (dt.date.today() - dt.timedelta(days=2)):
            df = cached
        else:
            df = cached
    else:
        df = None

    # 取得 or 追記
    if df is None or df.index.max() < (dt.date.today() - dt.timedelta(days=2)):
        try:
            fresh = pdr.DataReader(sym, "stooq")
            fresh = fresh.sort_index()
            # stooq列名: Open, High, Low, Close, Volume（Adj Close なし）
            # キャッシュとマージ
            if df is not None and not df.empty:
                df = pd.concat([df, fresh]).groupby(level=0).last().sort_index()
            else:
                df = fresh
            _write_cache(path, df)
        except Exception:
            # 取得失敗時：キャッシュがあればそれを返す
            if cached is not None and not cached.empty:
                df = cached
            else:
                # 空のDF
                return pd.DataFrame(columns=["Open","High","Low","Close","Volume"])

    # 期間絞り
    start = pd.Timestamp(dt.date.today() - dt.timedelta(days=lookback_days))
    df = df[df.index >= start]
    # 欠損を前方埋め（出来高は0を許容）
    df["Volume"] = df["Volume"].fillna(0)
    return df[["Open","High","Low","Close","Volume"]]
