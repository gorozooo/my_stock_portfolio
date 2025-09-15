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
_TSE_JSON_PATH = os.environ.get(
    "TSE_JSON_PATH",
    os.path.join(BASE_DIR, "data", "tse_list.json"),
)
_TSE_CSV_PATH = os.environ.get(
    "TSE_CSV_PATH",
    os.path.join(BASE_DIR, "data", "tse_list.csv"),
)
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
# JSON/CSV ロード（形式ゆらぎに強く）
#   JSON は以下のどれでもOK:
#     1) [{"code": "8058", "name": "三菱商事"}, ...]
#     2) {"8058": "三菱商事", ...}
#     3) {"items": [...上記1の配列...] }
# =========================================================
def _read_tse_json(path: str) -> Optional[pd.DataFrame]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        # 2) dict の「コード→名称」形式
        if isinstance(obj, dict) and obj and all(
            isinstance(k, str) and isinstance(v, str) for k, v in obj.items()
        ):
            rows = [{"code": k, "name": v} for k, v in obj.items()]
            df = pd.DataFrame(rows)
        # 1) list[dict] 形式
        elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
            df = pd.DataFrame(obj)
        # 3) ラッパーの中に items 等で入っている
        elif isinstance(obj, dict) and "items" in obj and isinstance(obj["items"], list):
            df = pd.DataFrame(obj["items"])
        else:
            raise ValueError("Unexpected JSON structure")

        # 列名解決
        df = df.rename(columns={c: c.lower() for c in df.columns})
        # code/name を解決（ticker/symbol/jp_name 等も許容）
        code_col = None
        for key in ("code", "ticker", "symbol"):
            if key in df.columns:
                code_col = key
                break
        name_col = None
        for key in ("name", "jp_name", "company", "company_name"):
            if key in df.columns:
                name_col = key
                break
        if not (code_col and name_col):
            raise ValueError("JSON must contain 'code' and 'name'-like columns")

        df = df[[code_col, name_col]].rename(columns={code_col: "code", name_col: "name"})
        return df
    except Exception as e:
        _d(f"failed to load json: {e}")
        return None


def _read_tse_csv(path: str) -> Optional[pd.DataFrame]:
    if not os.path.isfile(path):
        return None
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
        df = df.rename(columns={c: c.lower() for c in df.columns})
        if not {"code", "name"}.issubset(df.columns):
            raise ValueError("CSV must contain 'code' and 'name'")
        return df[["code", "name"]]
    except Exception as e:
        _d(f"failed to load csv: {e}")
        return None


def _load_tse_map_if_needed() -> None:
    global _TSE_MAP, _TSE_MTIME

    json_m = os.path.getmtime(_TSE_JSON_PATH) if os.path.isfile(_TSE_JSON_PATH) else 0.0
    csv_m = os.path.getmtime(_TSE_CSV_PATH) if os.path.isfile(_TSE_CSV_PATH) else 0.0

    if not _TSE_ALWAYS_RELOAD and _TSE_MAP and _TSE_MTIME == (json_m, csv_m):
        return

    # JSON 優先 → CSV
    df = _read_tse_json(_TSE_JSON_PATH) or _read_tse_csv(_TSE_CSV_PATH)
    if df is None:
        _TSE_MAP = {}
        _TSE_MTIME = (json_m, csv_m)
        return

    # 正規化
    df["code"] = df["code"].astype(str).map(_clean_text).str.upper()
    df["name"] = df["name"].astype(str).map(_clean_text)
    df = df.dropna().drop_duplicates(subset=["code"])

    _TSE_MAP = {row["code"]: row["name"] for _, row in df.iterrows()}
    _TSE_MTIME = (json_m, csv_m)
    _d(f"loaded map ({len(_TSE_MAP)} rows)")


def _lookup_name_jp_from_list(ticker: str) -> Optional[str]:
    _load_tse_map_if_needed()
    if not _TSE_MAP or not ticker:
        return None
    head = ticker.upper().split(".", 1)[0]  # '167A.T' -> '167A'
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
    - ドット付きはそのまま大文字化
    - 英数字4–5桁だけなら日本株とみなし「.T」を付ける（例: 7203, 167A）
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
    2) 見つからなければ **英語名に落とさず**、日本株はコード（ドット前）を返す
    3) 海外等のみ yfinance にフォールバック（失敗したら最後はティッカー文字列）
    """
    name = _lookup_name_jp_from_list(ticker)
    if isinstance(name, str) and name.strip():
        return name.strip()

    # 日本株っぽいならコードのみ返す（英語名に落とさない）
    head = ticker.upper().split(".", 1)[0]
    if _JP_ALNUM.match(head):
        return head

    # 海外銘柄など
    try:
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return _clean_text(name)
    except Exception:
        pass
    return ticker


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
# ヘルパ
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


# =========================================================
# メイン判定
# =========================================================
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