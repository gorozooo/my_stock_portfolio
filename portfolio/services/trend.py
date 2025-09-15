from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict
import os
import re
import json
import unicodedata

import numpy as np
import pandas as pd
import yfinance as yf

# =====================================================================
# 日本語銘柄名 辞書ローダ
# - 既定: project_root/data/tse_list.csv（必要なら環境変数で上書き）
# - フォールバック: project_root/data/tse_list.json（update_tse_list が出力）
# - 環境変数:
#     TSE_CSV_PATH            … CSV のパス
#     TSE_JSON_PATH           … JSON のパス
#     TSE_CSV_ALWAYS_RELOAD=1 … 毎回再読込（開発向け）
#     TSE_DEBUG=1             … ルックアップ状況をprintで出す
# =====================================================================

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
_TSE_CSV_PATH = os.environ.get("TSE_CSV_PATH", os.path.join(ROOT_DIR, "data", "tse_list.csv"))
_TSE_JSON_PATH = os.environ.get("TSE_JSON_PATH", os.path.join(ROOT_DIR, "data", "tse_list.json"))
_ALWAYS_RELOAD = os.environ.get("TSE_CSV_ALWAYS_RELOAD", "0") == "1"
_DEBUG = os.environ.get("TSE_DEBUG", "0") == "1"

# キャッシュ
_TSE_MAP: Dict[str, str] = {}
_TSE_SRC: str = ""           # "csv" or "json"
_TSE_MTIME: float = 0.0


# ----------------------------
# テキストクレンジング
# ----------------------------
def _clean_text(s: str) -> str:
    """
    不可視PUA「」を含む、制御/フォーマット/私用領域などを除去し、
    NFKC正規化して空白を整形して返す。
    """
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFKC", s)

    # Unicodeカテゴリが Other(C*) の文字を全て除去: Cc, Cf, Co, Cs, Cn
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("C"))

    # 全角スペース→半角、空白たたみ
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ----------------------------
# データ読み込み
# ----------------------------
def _load_tse_map_from_csv(path: str) -> Dict[str, str]:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"code": str, "name": str})
    cols = {c.lower(): c for c in df.columns}
    code_col = cols.get("code")
    name_col = cols.get("name")
    if not code_col or not name_col:
        raise ValueError("CSV に 'code' と 'name' 列が必要です")

    df[code_col] = df[code_col].astype(str).map(_clean_text)
    df[name_col] = df[name_col].astype(str).map(_clean_text)

    return {
        row[code_col]: row[name_col]
        for _, row in df.iterrows()
        if row[code_col] and row[name_col]
    }


def _load_tse_map_from_json(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    # 形式は {"7011": "三菱重工業", ...} or [{"code": "...","name":"..."}] の両対応
    if isinstance(obj, dict):
        items = obj.items()
    elif isinstance(obj, list):
        items = ((str(_clean_text(e.get("code", ""))), _clean_text(e.get("name", ""))) for e in obj)
    else:
        items = []

    mp: Dict[str, str] = {}
    for k, v in items:
        k = _clean_text(str(k))
        v = _clean_text(str(v))
        if k and v:
            mp[k] = v
    return mp


def _load_tse_map_if_needed() -> None:
    """
    CSV/JSON を読み、更新時刻が変わっていれば再読込。
    どちらも読めなければ空マップ。
    """
    global _TSE_MAP, _TSE_MTIME, _TSE_SRC

    # 既存キャッシュが有効ならスキップ
    targets = []
    if os.path.isfile(_TSE_CSV_PATH):
        targets.append(("csv", _TSE_CSV_PATH, os.path.getmtime(_TSE_CSV_PATH)))
    if os.path.isfile(_TSE_JSON_PATH):
        targets.append(("json", _TSE_JSON_PATH, os.path.getmtime(_TSE_JSON_PATH)))

    if not targets:
        if _DEBUG:
            print("[TSE] no source files found")
        return

    # 一番新しい方を採用
    src, path, mtime = max(targets, key=lambda t: t[2])

    if not _ALWAYS_RELOAD and _TSE_MAP and _TSE_SRC == src and _TSE_MTIME == mtime:
        return  # 変更なし

    try:
        if src == "csv":
            mp = _load_tse_map_from_csv(path)
        else:
            mp = _load_tse_map_from_json(path)

        _TSE_MAP = mp
        _TSE_SRC = src
        _TSE_MTIME = mtime
        if _DEBUG:
            print(f"[TSE] loaded {src} ({len(_TSE_MAP)} rows)")
    except Exception as e:
        if _DEBUG:
            print(f"[TSE] load failed: {e}")
        _TSE_MAP = {}
        _TSE_SRC = ""
        _TSE_MTIME = 0.0


# ----------------------------
# ルックアップ
# ----------------------------
def _lookup_name_jp_from_dict(ticker: str) -> Optional[str]:
    _load_tse_map_if_needed()
    if not _TSE_MAP:
        return None

    t = (ticker or "").upper().strip()
    if not t:
        return None

    numeric = t.split(".", 1)[0]
    if numeric.isdigit() and len(numeric) in (4, 5):
        name = _TSE_MAP.get(numeric)
        if _DEBUG:
            print(f"[TSE] lookup {numeric} -> {name!r}")
        if name:
            return _clean_text(name)
    else:
        if _DEBUG:
            print(f"[TSE] skip non-TSE ticker: {ticker}")
    return None


# =====================================================================
# ティッカー正規化 / 名前取得
# =====================================================================

def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    if t.isdigit() and len(t) in (4, 5):
        return f"{t}.T"
    # 万一「7203Ｔ」など全角末尾が来た場合の保険（数字だけ抽出して .T 付与）
    nums = re.sub(r"\D", "", t)
    if nums.isdigit() and len(nums) in (4, 5):
        return f"{nums}.T"
    return t


def _fetch_name_prefer_jp(ticker: str) -> str:
    # 1) 自前辞書（CSV/JSON）
    name_csv = _lookup_name_jp_from_dict(ticker)
    if name_csv:
        return name_csv

    # 2) 日本株コードなら数字コードをそのまま見せる（英語名に落とさない）
    t = (ticker or "").upper().strip()
    numeric = t.split(".", 1)[0]
    if numeric.isdigit() and len(numeric) in (4, 5):
        return numeric

    # 3) 海外等は yfinance
    try:
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return _clean_text(name)
    except Exception:
        pass

    return ticker


# =====================================================================
# 結果スキーマ
# =====================================================================

@dataclass
class TrendResult:
    ticker: str
    name: str
    asof: str
    days: int
    signal: str
    reason: str
    slope: float
    slope_annualized_pct: float
    ma_short: Optional[float]
    ma_long: Optional[float]


# =====================================================================
# メイン判定
# =====================================================================

def detect_trend(
    ticker: str,
    days: int = 60,
    ma_short_win: int = 10,
    ma_long_win: int = 30,
) -> TrendResult:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        raise ValueError("ticker is required")

    # 市場休日考慮で余裕を持って取得
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

    def _as_scalar(v):
        if isinstance(v, pd.Series):
            return v.iloc[-1]
        if isinstance(v, np.ndarray):
            if v.size == 0:
                return None
            return v.item() if v.size == 1 else v[-1]
        return v

    def _to_float_or_none(v) -> Optional[float]:
        v = _as_scalar(v)
        try:
            if pd.isna(v):
                return None
        except Exception:
            pass
        try:
            return float(v)
        except Exception:
            return None

    ma_s = _to_float_or_none(ma_short.iloc[-1])
    ma_l = _to_float_or_none(ma_long.iloc[-1])

    # 線形回帰
    y = s.values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _b = np.polyfit(x, y, 1)

    # 年率換算（営業日 ≒ 252）
    last_price = y[-1]
    slope_daily_pct = (k / last_price) * 100.0 if last_price else 0.0
    slope_ann_pct = slope_daily_pct * 252.0

    # シグナル
    signal = "FLAT"
    reason = "傾きが小さいため様子見"
    if slope_ann_pct >= 5.0:
        signal = "UP"
        reason = "回帰傾き(年率換算)が正で大きめ"
    elif slope_ann_pct <= -5.0:
        signal = "DOWN"
        reason = "回帰傾き(年率換算)が負で大きめ"

    if (ma_s is not None) and (ma_l is not None):
        if ma_s > ma_l and signal == "FLAT":
            signal, reason = "UP", "短期線が長期線を上回る(ゴールデンクロス気味)"
        elif ma_s < ma_l and signal == "FLAT":
            signal, reason = "DOWN", "短期線が長期線を下回る(デッドクロス気味)"

    asof = s.index[-1].date().isoformat()
    name = _clean_text(_fetch_name_prefer_jp(ticker))

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