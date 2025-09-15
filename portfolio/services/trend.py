# portfolio/services/trend.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Tuple

import os
import re
import json
import unicodedata

import numpy as np
import pandas as pd
import yfinance as yf

# =========================================================
# 設定（環境変数で上書き可）
# =========================================================
BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# 東証の「コード→日本語名」辞書（JSON 推奨。 update_tse_list で作成）
TSE_JSON_PATH = os.environ.get(
    "TSE_JSON_PATH",
    os.path.join(BASE_DIR, "data", "tse_list.json"),
)
# CSV を置いている場合のフォールバック（任意）
TSE_CSV_PATH = os.environ.get(
    "TSE_CSV_PATH",
    os.path.join(BASE_DIR, "data", "tse_list.csv"),
)

# 辞書の強制リロード（開発時用） 1=毎回読み直し
TSE_ALWAYS_RELOAD = os.environ.get("TSE_CSV_ALWAYS_RELOAD", "0") == "1"

# デバッグ出力 1=有効
TSE_DEBUG = os.environ.get("TSE_DEBUG", "0") == "1"


def _d(msg: str) -> None:
    """デバッグ出力（TSE_DEBUG=1 のときだけ）"""
    if TSE_DEBUG:
        print(f"[TSE] {msg}")


# =========================================================
# 文字列クレンジング
# =========================================================
def _clean_text(s: str) -> str:
    """不可視文字などの混入を除去 + Unicode 正規化"""
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\u200B-\u200D\uFEFF]", "", s)   # zero width & BOM
    s = re.sub(r"[\uFE00-\uFE0F]", "", s)         # variation selectors
    s = re.sub(r"[\u0000-\u001F\u007F]", "", s)   # control chars + DEL
    s = re.sub(r"[\uE000-\uF8FF]", "", s)         # Private Use Area
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# =========================================================
# 日本語銘柄名辞書（JSON 優先 / CSV フォールバック）
# trend.py 単体で完結させる（tse.py には依存しない）
# =========================================================
_TSE_NAME_MAP: Dict[str, str] = {}
_TSE_MTIME: Tuple[float, float] = (0.0, 0.0)  # (json_mtime, csv_mtime)


def _load_name_map_if_needed() -> None:
    """辞書ファイルが更新されていれば再読込する。"""
    global _TSE_NAME_MAP, _TSE_MTIME

    json_m = os.path.getmtime(TSE_JSON_PATH) if os.path.isfile(TSE_JSON_PATH) else 0.0
    csv_m = os.path.getmtime(TSE_CSV_PATH) if os.path.isfile(TSE_CSV_PATH) else 0.0

    if _TSE_NAME_MAP and _TSE_MTIME == (json_m, csv_m) and not TSE_ALWAYS_RELOAD:
        return

    # まず JSON を読む
    name_map: Dict[str, str] = {}
    if os.path.isfile(TSE_JSON_PATH):
        try:
            with open(TSE_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)  # list[{"code": "...", "name": "..."}] を想定
            for row in data:
                code = _clean_text(str(row.get("code", "")).upper())
                name = _clean_text(str(row.get("name", "")))
                if code and name:
                    name_map[code] = name
            _d(f"loaded json ({len(name_map)} rows)")
        except Exception as e:
            _d(f"failed to load json: {e}")

    # JSON が無い/失敗した場合は CSV
    if not name_map and os.path.isfile(TSE_CSV_PATH):
        try:
            df = pd.read_csv(TSE_CSV_PATH, encoding="utf-8-sig", dtype=str)
            df = df.rename(columns={c: c.lower() for c in df.columns})
            if {"code", "name"}.issubset(df.columns):
                for _, row in df[["code", "name"]].dropna().iterrows():
                    code = _clean_text(str(row["code"]).upper())
                    name = _clean_text(str(row["name"]))
                    if code and name:
                        name_map[code] = name
                _d(f"loaded csv ({len(name_map)} rows)")
        except Exception as e:
            _d(f"failed to load csv: {e}")

    _TSE_NAME_MAP = name_map
    _TSE_MTIME = (json_m, csv_m)


def _lookup_name_jp(ticker: str) -> Optional[str]:
    """ティッカー '8058.T' → '8058' で辞書を引く。"""
    _load_name_map_if_needed()
    if not _TSE_NAME_MAP or not ticker:
        return None
    head = ticker.upper().split(".", 1)[0]  # 例: '167A.T' -> '167A'
    name = _TSE_NAME_MAP.get(head)
    if TSE_DEBUG:
        _d(f"lookup {head} -> {repr(name)}")
    return name


# =========================================================
# ティッカー正規化 / 名称取得
# =========================================================
_JP_ALNUM = re.compile(r"^[0-9A-Z]{4,5}$")


def _normalize_ticker(raw: str) -> str:
    """
    入力を正規化。
    - ドット付きはそのまま大文字化（AAPL, 7203.T 等）
    - 英数字4〜5桁だけなら日本株とみなし `.T` を付与（7203, 167A）
    - それ以外は大文字化のみ
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
    名称の優先順位:
    1) 東証辞書（JSON/CSV）
    2) yfinance の shortName/longName/name
    3) 最後はティッカーのドット前（日本株ならコード）
    """
    name = _lookup_name_jp(ticker)
    if isinstance(name, str) and name.strip():
        return _clean_text(name)

    try:
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return _clean_text(name)
    except Exception:
        pass

    return ticker.upper().split(".", 1)[0] or ticker


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
    """Series/Scalar を安全に float/None に変換（ambiguous 回避）。"""
    try:
        if isinstance(v, pd.Series):
            # 単一要素 Series を想定
            if v.empty:
                return None
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
    """
    直近 N 日の終値で線形回帰の傾きと移動平均を見て
    シンプルに UP/DOWN/FLAT を返す。
    """
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

    # 移動平均
    ma_short = s.rolling(ma_short_win).mean()
    ma_long = s.rolling(ma_long_win).mean()
    ma_s = _to_float_or_none(ma_short.iloc[[-1]])
    ma_l = _to_float_or_none(ma_long.iloc[[-1]])

    # 線形回帰（x は 0..n-1）
    y = s.values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _b = np.polyfit(x, y, 1)

    # 年率換算の概算（営業日 ~ 252日）
    last_price = y[-1]
    slope_daily_pct = (k / last_price) * 100.0 if last_price else 0.0
    slope_ann_pct = slope_daily_pct * 252.0

    # シグナル判定（シンプル基準）
    signal = "FLAT"
    reason = "傾きが小さいため様子見"
    if slope_ann_pct >= 5.0:
        signal, reason = "UP", "回帰傾き(年率換算)が正で大きめ"
    elif slope_ann_pct <= -5.0:
        signal, reason = "DOWN", "回帰傾き(年率換算)が負で大きめ"

    # MA クロスで補強（元が FLAT のときに決める）
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