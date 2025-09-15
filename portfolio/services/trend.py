# portfolio/services/trend.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Tuple
import os
import re
import unicodedata

import numpy as np
import pandas as pd
import yfinance as yf

# =========================================================
# 設定（環境変数で上書き可）
# =========================================================
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
_TSE_JSON_PATH = os.environ.get("TSE_JSON_PATH", os.path.join(BASE_DIR, "data", "tse_list.json"))
_TSE_CSV_PATH  = os.environ.get("TSE_CSV_PATH",  os.path.join(BASE_DIR, "data", "tse_list.csv"))
_TSE_ALWAYS_RELOAD = os.environ.get("TSE_CSV_ALWAYS_RELOAD", "0") == "1"
_TSE_DEBUG = os.environ.get("TSE_DEBUG", "0") == "1"

# キャッシュ
_TSE_MAP: Dict[str, str] = {}
_TSE_MTIME: Tuple[float, float] = (0.0, 0.0)  # (json_mtime, csv_mtime)


def _d(msg: str) -> None:
    if _TSE_DEBUG:
        print(f"[TSE] {msg}")


# =========================================================
# テキストクレンジング
# =========================================================
def _clean_text(s: str) -> str:
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\u200B-\u200D\uFEFF]", "", s)   # zero width & BOM
    s = re.sub(r"[\uFE00-\uFE0F]", "", s)         # variation selectors
    s = re.sub(r"[\u0000-\u001F\u007F]", "", s)   # control chars + DEL
    s = re.sub(r"[\uE000-\uF8FF]", "", s)         # PUA
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# =========================================================
# 日本語銘柄名ローダ（JSON優先、なければCSV）
#   どちらも "code","name" を想定（codeは英数字4–5桁も可）
# =========================================================
def _load_tse_map_if_needed() -> None:
    global _TSE_MAP, _TSE_MTIME
    json_m, csv_m = 0.0, 0.0
    if os.path.isfile(_TSE_JSON_PATH):
        json_m = os.path.getmtime(_TSE_JSON_PATH)
    if os.path.isfile(_TSE_CSV_PATH):
        csv_m = os.path.getmtime(_TSE_CSV_PATH)

    if not _TSE_ALWAYS_RELOAD and _TSE_MAP and _TSE_MTIME == (json_m, csv_m):
        return

    # まず JSON
    df = None
    if os.path.isfile(_TSE_JSON_PATH):
        try:
            d = pd.read_json(_TSE_JSON_PATH, orient="records")
            # 記録の形式に合わせて柔軟に列名を解決
            cols = {c.lower(): c for c in d.columns}
            code = cols.get("code") or cols.get("ticker") or cols.get("symbol")
            name = cols.get("name") or cols.get("jp_name") or cols.get("company")
            if code and name:
                d = d[[code, name]].rename(columns={code: "code", name: "name"})
                df = d
                _d(f"loaded json ({len(df)} rows)")
        except Exception:
            pass

    # JSONがダメならCSV
    if df is None and os.path.isfile(_TSE_CSV_PATH):
        try:
            d = pd.read_csv(_TSE_CSV_PATH, encoding="utf-8-sig", dtype=str)
            cols = {c.lower(): c for c in d.columns}
            code = cols.get("code")
            name = cols.get("name")
            if code and name:
                d = d[[code, name]].rename(columns={code: "code", name: "name"})
                df = d
                _d(f"loaded csv ({len(df)} rows)")
        except Exception:
            pass

    if df is None:
        _TSE_MAP = {}
        _TSE_MTIME = (json_m, csv_m)
        return

    df["code"] = df["code"].astype(str).map(_clean_text)
    df["name"] = df["name"].astype(str).map(_clean_text)
    # code は英数字4–5桁を想定（例: 167A, 7203）
    _TSE_MAP = {row["code"].upper(): row["name"] for _, row in df.iterrows() if row["code"] and row["name"]}
    _TSE_MTIME = (json_m, csv_m)


def _lookup_name_jp_from_list(ticker: str) -> Optional[str]:
    """
    事前にロード済みの _TSE_MAP から日本語名を返す。
    ルックアップキーは「ドット前の英数字4–5桁（大文字）をそのまま」。
    """
    _load_tse_map_if_needed()
    if not _TSE_MAP:
        return None
    if not ticker:
        return None
    head = ticker.upper().split(".", 1)[0]  # 例: '167A.T' -> '167A'
    name = _TSE_MAP.get(head)
    if _TSE_DEBUG:
        _d(f"lookup {head} -> {repr(name)}")
    return name


# =========================================================
# ティッカー正規化 / 名前取得
# =========================================================
_JP_ALNUM = re.compile(r"^[0-9A-Z]{4,5}$")

def _normalize_ticker(raw: str) -> str:
    """
    入力を正規化。
    - ドット付きはそのまま大文字化
    - 英数字4–5桁だけなら日本株とみなし「.T」を付ける（例: 7203, 167A）
    - 上記以外は大文字化のみ
    """
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    if _JP_ALNUM.match(t):
        return f"{t}.T"
    return t


def _fetch_name_prefer_jp(ticker: str) -> str:
    """
    1) JP辞書（JSON/CSV）最優先
    2) 辞書になければ yfinance
    3) 最後の手段はティッカー
    """
    name = _lookup_name_jp_from_list(ticker)
    if isinstance(name, str) and name.strip():
        return name.strip()

    try:
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return _clean_text(name)
    except Exception:
        pass

    # 日本株ならドット前を返す（英数字コード）
    head = ticker.upper().split(".", 1)[0]
    return head or ticker


# =========================================================
# 結果スキーマ
# =========================================================
@dataclass
class TrendResult:
    ticker: str
    name: str
    asof: str
    days: int
    signal: str         # 'UP' | 'DOWN' | 'FLAT'
    reason: str
    slope: float
    slope_annualized_pct: float
    ma_short: Optional[float]
    ma_long: Optional[float]


# =========================================================
# メイン判定
# =========================================================
def _to_float_or_none(v) -> Optional[float]:
    try:
        if isinstance(v, pd.Series):
            v = v.iloc[0]
        if pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def detect_trend(
    ticker: str,
    days: int = 60,
    ma_short_win: int = 10,
    ma_long_win: int = 30,
) -> TrendResult:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        raise ValueError("ticker is required")

    # 休日を考慮して少し余裕を持って取得
    period_days = max(days + 30, 120)
    df = yf.download(ticker, period=f"{period_days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("価格データを取得できませんでした")

    s = df["Close"].dropna()
    if s.empty:
        raise ValueError("終値データが空でした")

    s = s.tail(days)
    if len(s) < max(15, ma_long_win):
        raise ValueError(f"データ日数が不足しています（取得: {len(s)}日）")

    ma_short = s.rolling(ma_short_win).mean()
    ma_long  = s.rolling(ma_long_win).mean()

    ma_s = _to_float_or_none(ma_short.iloc[[-1]])
    ma_l = _to_float_or_none(ma_long.iloc[[-1]])

    y = s.values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _b = np.polyfit(x, y, 1)

    last_price = y[-1]
    slope_daily_pct = (k / last_price) * 100.0 if last_price else 0.0
    slope_ann_pct = slope_daily_pct * 252.0

    signal = "FLAT"
    reason = "傾きが小さいため様子見"
    if slope_ann_pct >= 5.0:
        signal, reason = "UP", "回帰傾き(年率換算)が正で大きめ"
    elif slope_ann_pct <= -5.0:
        signal, reason = "DOWN", "回帰傾き(年率換算)が負で大きめ"

    if (ma_s is not None) and (ma_l is not None) and signal == "FLAT":
        if ma_s > ma_l:
            signal, reason = "UP", "短期線が長期線を上回る(ゴールデンクロス気味)"
        elif ma_s < ma_l:
            signal, reason = "DOWN", "短期線が長期線を下回る(デッドクロス気味)"

    asof = s.index[-1].date().isoformat()
    name = _fetch_name_prefer_jp(ticker)

    return TrendResult(
        ticker=ticker,
        name=name,
        asof=asof,
        days=int(len(s)),
        signal=signal,
        reason=reason,
        slope=float(k),
        slope_annualized_pct=float(slope_ann_pct),
        ma_short=ma_s,
        ma_long=ma_l,
    )