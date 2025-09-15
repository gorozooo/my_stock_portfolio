from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict
import os
import re
import datetime as dt

import numpy as np
import pandas as pd
import yfinance as yf


# =====================================================================
# 日本語銘柄名 CSV ローダ（data/tse_list.csv を想定）
# - 形式: ヘッダあり、少なくとも "code","name" の2列
# - 文字コード: UTF-8 (BOMあり/なし可)
# - CSV に紛れがちなゼロ幅スペース等も除去してマップ化
# =====================================================================

# CSV の既定パス（必要なら環境変数で上書き）
_TSE_CSV_PATH = os.environ.get(
    "TSE_CSV_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tse_list.csv"),
)

# モジュール内キャッシュ
_TSE_MAP: Dict[str, str] = {}
_TSE_CSV_MTIME: float = 0.0

# ゼロ幅系・BOM を落とす正規表現
_ZW_RE = re.compile(r"[\u200B-\u200D\uFEFF]")

def _clean_text(s: str) -> str:
    """前後の空白除去 + ゼロ幅文字除去。"""
    if not isinstance(s, str):
        s = str(s) if s is not None else ""
    s = s.strip()
    s = _ZW_RE.sub("", s)
    return s

def _clean_code_for_key(s: str) -> str:
    """
    コード列用の正規化:
    - 空白類を全除去
    - ゼロ幅文字除去
    """
    s = _clean_text(s)
    s = re.sub(r"\s+", "", s)
    return s

def _load_tse_map_if_needed() -> None:
    """
    CSV があれば読み込み、更新時刻が変わっていれば再読込する。
    無ければ何もしない（yfinance にフォールバック）。
    """
    global _TSE_MAP, _TSE_CSV_MTIME

    if not os.path.isfile(_TSE_CSV_PATH):
        return

    try:
        mtime = os.path.getmtime(_TSE_CSV_PATH)
        if _TSE_MAP and _TSE_CSV_MTIME == mtime:
            return  # 変更なし

        df = pd.read_csv(
            _TSE_CSV_PATH,
            encoding="utf-8-sig",
            dtype=str,
        )

        # 列名（大小文字ゆらぎ対応）
        cols_l = {c.lower(): c for c in df.columns}
        code_col = cols_l.get("code")
        name_col = cols_l.get("name")
        if not code_col or not name_col:
            _TSE_MAP = {}
            _TSE_CSV_MTIME = 0.0
            return

        # 正規化
        df[code_col] = df[code_col].map(_clean_code_for_key)
        df[name_col] = df[name_col].map(_clean_text)

        # マップ化（例: "7011" -> "三菱重工業"）
        _TSE_MAP = {
            row[code_col]: row[name_col]
            for _, row in df.iterrows()
            if row[code_col] and row[name_col]
        }
        _TSE_CSV_MTIME = mtime
    except Exception:
        # CSV が壊れていても落ちないように、マップは空に戻す
        _TSE_MAP = {}
        _TSE_CSV_MTIME = 0.0


def _lookup_name_jp_from_csv(ticker: str) -> Optional[str]:
    """
    ティッカーが東証（nnnn or nnnn.T 等）なら CSV から日本語名を返す。
    見つからなければ None。
    """
    _load_tse_map_if_needed()
    if not _TSE_MAP:
        return None

    t = (ticker or "").upper().strip()
    if not t:
        return None

    # "7203" / "7203.T" / "7203.TK" など → 数字部分
    numeric = t.split(".", 1)[0]
    numeric = _clean_code_for_key(numeric)
    if numeric.isdigit() and 4 <= len(numeric) <= 5:
        return _TSE_MAP.get(numeric)
    return None


# =====================================================================
# ティッカー正規化 / 名前取得
# =====================================================================

def _normalize_ticker(raw: str) -> str:
    """
    入力を正規化。
    - 4〜5桁の数字だけ、または先頭が数字だらけの場合は日本株とみなし「.T」を付与
      例: '7203' -> '7203.T', ' 7203 ' -> '7203.T'
    - すでにサフィックスがある場合や英米株などは大文字化のみ
    """
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    # 非数字を除去して判定（ユーザ入力の混入に強く）
    digits_only = re.sub(r"\D", "", t)
    if digits_only.isdigit() and 4 <= len(digits_only) <= 5:
        return f"{digits_only}.T"
    return t


def _fetch_name_prefer_jp(ticker: str) -> str:
    """
    1) CSV（全銘柄日本語辞書）があれば最優先
    2) 日本株ティッカー (nnnn.T) で CSV に無ければコード文字列を返す
    3) それ以外は yfinance にフォールバック
    """
    # まず CSV
    name_csv = _lookup_name_jp_from_csv(ticker)
    if isinstance(name_csv, str) and name_csv.strip():
        return _clean_text(name_csv)

    # 日本株コード (nnnn.T) の場合 → CSV優先で無ければ ticker をそのまま返す
    t = (ticker or "").upper().strip()
    numeric = t.split(".", 1)[0]
    if numeric.isdigit() and 4 <= len(numeric) <= 5:
        return numeric  # 例: "8058"

    # フォールバック: 海外株など
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
    name: str                   # 日本語名を想定（無ければ英語 or ティッカー）
    asof: str                   # 例: '2025-09-15'
    days: int                   # 直近使用日数
    signal: str                 # 'UP' | 'DOWN' | 'FLAT'
    reason: str
    slope: float                # 1日あたりの回帰傾き（終値）
    slope_annualized_pct: float # 年率換算(%)
    ma_short: Optional[float]   # 短期MAの最新値
    ma_long: Optional[float]    # 長期MAの最新値


# =====================================================================
# メイン判定
# =====================================================================

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

    # データ取得（市場休日も考慮して余裕を持って period を長めに）
    period_days = max(days + 30, 120)
    df = yf.download(ticker, period=f"{period_days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("価格データを取得できませんでした")

    # 終値のみ
    s = df["Close"].dropna()
    if s.empty:
        raise ValueError("終値データが空でした")

    # 直近 'days' 営業日のみ
    s = s.tail(days)

    if len(s) < max(15, ma_long_win):
        raise ValueError(f"データ日数が不足しています（取得: {len(s)}日）")

    # 移動平均
    ma_short = s.rolling(ma_short_win).mean()
    ma_long = s.rolling(ma_long_win).mean()

    # 直近の有効値（NaN を飛ばして最後の値）を安全に取得
    def _last_valid(series) -> Optional[float]:
        try:
            v = series.dropna().iloc[-1]
            return float(v)
        except Exception:
            return None

    ma_s = _last_valid(ma_short)
    ma_l = _last_valid(ma_long)

    # 線形回帰（x は 0..n-1）
    y = s.values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _b = np.polyfit(x, y, 1)  # 傾き k

    # 年率換算の概算（営業日 ~ 252日）
    last_price = y[-1]
    slope_daily_pct = (k / last_price) * 100.0 if last_price else 0.0
    slope_ann_pct = slope_daily_pct * 252.0

    # シグナル判定（シンプル基準）
    signal = "FLAT"
    reason = "傾きが小さいため様子見"
    if slope_ann_pct >= 5.0:
        signal = "UP"
        reason = "回帰傾き(年率換算)が正で大きめ"
    elif slope_ann_pct <= -5.0:
        signal = "DOWN"
        reason = "回帰傾き(年率換算)が負で大きめ"

    # MA クロスで補強
    if (ma_s is not None) and (ma_l is not None):
        if ma_s > ma_l and signal == "FLAT":
            signal, reason = "UP", "短期線が長期線を上回る(ゴールデンクロス気味)"
        elif ma_s < ma_l and signal == "FLAT":
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