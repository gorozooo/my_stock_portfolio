# aiapp/services/bars_5m.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf
from django.conf import settings


JST = timezone(timedelta(hours=9))

# キャッシュ保存先: MEDIA_ROOT/aiapp/bars_5m/
BARS_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "bars_5m"
BARS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Bars5mResult:
    code: str
    start_date: date
    end_date: date
    df: pd.DataFrame  # index: Datetime(JST), columns: [Open, High, Low, Close, Volume]


def _normalize_code(code: str) -> str:
    """
    JPX銘柄コード → yfinance 用シンボルに変換。
    すでに ".T" が付いていればそのまま。
    """
    code = str(code).strip()
    if not code:
        return code
    if code.endswith(".T"):
        return code
    return f"{code}.T"


def _day_range(start_date: date, end_date: date):
    """
    start_date 〜 end_date-1 まで 1日ずつ yield。
    """
    cur = start_date
    while cur < end_date:
        yield cur
        cur += timedelta(days=1)


def _cache_path_day(code: str, day: date) -> Path:
    """
    1営業日分の 5分足キャッシュファイルパス。
    例: 7508_2025-11-26_5m.csv
    """
    fname = f"{code}_{day.isoformat()}_5m.csv"
    return BARS_DIR / fname


def _download_5m_one_day(code: str, day: date) -> pd.DataFrame:
    """
    指定日の 5分足を yfinance から取得して DataFrame で返す。
    - インデックス: Datetime(JST)
    - カラム: ["Open", "High", "Low", "Close", "Volume"]
    """
    symbol = _normalize_code(code)
    if not symbol:
        return pd.DataFrame()

    start_dt = datetime(day.year, day.month, day.day, 0, 0, tzinfo=JST)
    end_dt = start_dt + timedelta(days=1)

    # FutureWarning 対応: auto_adjust を明示的に False にする
    df = yf.download(
        symbol,
        start=start_dt,
        end=end_dt,
        interval="5m",
        auto_adjust=False,   # ← これで FutureWarning を潰す
        progress=False,
        threads=False,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    # yfinance の仕様で MultiIndex になる場合があるので平坦化
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # 必要なカラムだけに絞る（足りない場合はそのまま）
    cols = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in cols if c in df.columns]].copy()

    # インデックスを JST に統一（すでに tz-aware のはずだが念のため）
    if df.index.tz is None:
        df.index = df.index.tz_localize(JST)
    else:
        df.index = df.index.tz_convert(JST)

    return df


def _load_5m_one_day_from_cache(code: str, day: date) -> pd.DataFrame:
    """
    1日分のキャッシュを CSV から読み込む。
    無ければ空 DataFrame。
    """
    path = _cache_path_day(code, day)
    if not path.exists():
        return pd.DataFrame()

    try:
        df = pd.read_csv(path, parse_dates=["Datetime"])
    except Exception:
        return pd.DataFrame()

    df.set_index("Datetime", inplace=True)

    # tz 情報が落ちている場合は JST を付与
    if df.index.tz is None:
        df.index = df.index.tz_localize(JST)
    else:
        df.index = df.index.tz_convert(JST)

    return df


def _save_5m_one_day_to_cache(code: str, day: date, df: pd.DataFrame) -> None:
    """
    1日分の5分足を CSV でキャッシュする。
    """
    if df is None or df.empty:
        return

    path = _cache_path_day(code, day)
    tmp_path = path.with_suffix(".tmp")

    to_save = df.copy()
    to_save = to_save.copy()
    to_save = to_save.reset_index()
    to_save.rename(columns={"index": "Datetime"}, inplace=True)

    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        to_save.to_csv(tmp_path, index=False)
        tmp_path.replace(path)
    except Exception:
        # キャッシュ失敗しても動作自体は続行したいので握りつぶす
        pass


def load_5m_bars(code: str, start_date: date, horizon_days: int = 1) -> Bars5mResult:
    """
    公開API:
      指定銘柄・start_date から horizon_days 日ぶんの 5分足を返す。

    - まず日別キャッシュ（CSV）を探す
    - 無ければ yfinance で取ってきて保存
    - 全日分を結合して返す
    """
    if horizon_days < 1:
        horizon_days = 1

    end_date = start_date + timedelta(days=horizon_days)
    frames: list[pd.DataFrame] = []

    for day in _day_range(start_date, end_date):
        cached = _load_5m_one_day_from_cache(code, day)
        if cached.empty:
            df = _download_5m_one_day(code, day)
            if not df.empty:
                _save_5m_one_day_to_cache(code, day, df)
                frames.append(df)
        else:
            frames.append(cached)

    if frames:
        df_all = pd.concat(frames).sort_index()
    else:
        df_all = pd.DataFrame()

    return Bars5mResult(code=code, start_date=start_date, end_date=end_date, df=df_all)


# 互換API（旧コードから使いやすいようにショートカットを用意）
def get_5m_bars(code: str, start_date: date, horizon_days: int = 1) -> pd.DataFrame:
    """
    load_5m_bars の df だけ返すラッパ。
    """
    return load_5m_bars(code, start_date, horizon_days).df