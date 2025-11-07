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
import pandas as pd
import datetime as dt
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
            # 念のため index を DatetimeIndex に強制
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            # 列も最低限を揃える
            cols = ["Open","High","Low","Close","Volume"]
            for c in cols:
                if c not in df.columns:
                    df[c] = pd.NA
            return df[cols].sort_index()
        except Exception:
            return None
    return None

def _write_cache(path: str, df: pd.DataFrame) -> None:
    # index が DatetimeIndex であることを前提に保存
    df.to_csv(path, index=True, date_format="%Y-%m-%d")

def _today_ts() -> pd.Timestamp:
    # “今日”の 00:00 を Timestamp で取得（naive）
    return pd.Timestamp.today().normalize()

def get_prices(code: str, lookback_days: int = 180) -> pd.DataFrame:
    """
    指定コードのOHLCV DataFrameを取得（Date index, Open High Low Close Volume）。
    まずキャッシュ、足りなければstooqから取得してキャッシュ更新。
    """
    sym = _symbol_for_stooq(code)
    # '^' はファイル名に使わない
    cache_name = sym.replace("^", "_idx_")
    path = _cache_path(cache_name)

    # キャッシュ読込
    cached = _read_cache(path)
    df = cached.copy() if cached is not None and not cached.empty else None

    # stooqの遅延を考慮：最新が「今日-2日」以前なら更新を試みる
    two_days_ago = _today_ts() - pd.Timedelta(days=2)
    needs_refresh = True
    if df is not None and not df.empty:
        last_ts = df.index.max()
        # ここを Timestamp 同士の比較に統一
        needs_refresh = bool(last_ts < two_days_ago)

    if needs_refresh:
        try:
            fresh = pdr.DataReader(sym, "stooq")
            # stooqは過去→現在の昇順を前提に安全化
            if not isinstance(fresh.index, pd.DatetimeIndex):
                fresh.index = pd.to_datetime(fresh.index)
            fresh = fresh.sort_index()
            fresh = fresh[["Open","High","Low","Close","Volume"]]

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

    # 期間絞り（Timestamp 基準）
    start_ts = _today_ts() - pd.Timedelta(days=lookback_days)
    df = df[df.index >= start_ts].copy()

    # 欠損処理：出来高は0を許容、価格は前方埋め（過度な穴を避けるため最小限）
    df["Volume"] = df["Volume"].fillna(0)
    for c in ["Open","High","Low","Close"]:
        if df[c].isna().any():
            df[c] = df[c].ffill()

    return df[["Open","High","Low","Close","Volume"]]
