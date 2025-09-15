from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict
import os
import datetime as dt

import numpy as np
import pandas as pd
import yfinance as yf


# =====================================================================
# 日本語銘柄名 CSV ローダ（data/tse_list.csv を想定）
# - 形式: ヘッダあり、少なくとも "code","name" の2列
# - 例:
#     code,name
#     7011,三菱重工業
#     7203,トヨタ自動車
# - 文字コードは UTF-8 (BOMあり/なし どちらでもOK)
# =====================================================================

# CSV の既定パス（必要なら環境変数で上書き）
_TSE_CSV_PATH = os.environ.get(
    "TSE_CSV_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tse_list.csv"),
)

# モジュール内キャッシュ
_TSE_MAP: Dict[str, str] = {}
_TSE_CSV_MTIME: float = 0.0


def _load_tse_map_if_needed() -> None:
    global _TSE_MAP, _TSE_CSV_MTIME

    if not os.path.isfile(_TSE_CSV_PATH):
        return

    mtime = os.path.getmtime(_TSE_CSV_PATH)
    if _TSE_MAP and _TSE_CSV_MTIME == mtime:
        return  # 変更なし

    try:
        df = pd.read_csv(
            _TSE_CSV_PATH,
            encoding="utf-8-sig",
            dtype=str,
        )
    except Exception:
        _TSE_MAP = {}
        _TSE_CSV_MTIME = 0.0
        return

    # 列名を正規化（小文字化 & 全角半角カナも対応）
    cols = {c.strip().lower(): c for c in df.columns}
    code_col = cols.get("code") or cols.get("ｺｰﾄﾞ") or cols.get("コード")
    name_col = cols.get("name") or cols.get("銘柄名") or cols.get("めいがらめい")

    if not code_col or not name_col:
        _TSE_MAP = {}
        _TSE_CSV_MTIME = 0.0
        return

    df[code_col] = df[code_col].astype(str).str.strip()
    df[name_col] = df[name_col].astype(str).str.strip()

    _TSE_MAP = {
        row[code_col]: row[name_col]
        for _, row in df.iterrows()
        if row[code_col] and row[name_col]
    }
    _TSE_CSV_MTIME = mtime


def _lookup_name_jp_from_csv(ticker: str) -> Optional[str]:
    """
    ティッカーが東証（nnnn or nnnn.T）なら CSV から日本語名を返す。
    見つからなければ None。
    """
    _load_tse_map_if_needed()
    if not _TSE_MAP:
        return None

    t = (ticker or "").upper().strip()
    if not t:
        return None

    # 「JP:7203」「7203.T」「7203.TK」などに対応して数字部分を抜く
    # まずコロンがあれば右側を優先
    if ":" in t:
        t = t.split(":", 1)[1]
    numeric = t.split(".", 1)[0]

    if numeric.isdigit() and len(numeric) in (4, 5):
        return _TSE_MAP.get(numeric)

    return None


# =====================================================================
# ティッカー正規化 / 名前取得
# =====================================================================

def _normalize_ticker(raw: str) -> str:
    """
    入力を正規化。
    - 4〜5桁の数字だけなら日本株とみなし「.T」を付与（例: '7203' -> '7203.T'）
    - すでにサフィックスがある場合や英米株などはそのまま（大文字化のみ）
    """
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t or ":" in t:
        # 既にサフィックス/プレフィックス付き（JP: など）ならそのまま大文字のみ
        return t
    if t.isdigit() and len(t) in (4, 5):
        return f"{t}.T"
    return t


def _fetch_name_prefer_jp(ticker: str) -> str:
    """
    1) CSV（全銘柄日本語辞書）があれば最優先
    2) なければ yfinance から取得（shortName/longName/name）
    3) それも無ければティッカーを返す
    """
    # まず CSV
    name_csv = _lookup_name_jp_from_csv(ticker)
    if isinstance(name_csv, str) and name_csv.strip():
        return name_csv.strip()

    # フォールバック: yfinance
    try:
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
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
    try:
        df = yf.download(
            ticker,
            period=f"{period_days}d",
            interval="1d",
            progress=False,
            auto_adjust=True,  # FutureWarning回避 & 分割配当の影響を除去
            threads=True,
        )
    except Exception as e:
        raise ValueError(f"価格データ取得に失敗しました: {e}") from e

    if df is None or df.empty:
        raise ValueError("価格データを取得できませんでした")

    # 終値のみ
    if "Close" not in df.columns:
        raise ValueError("価格データに終値列が見つかりませんでした")
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

    # 安全に float/None 化（ambiguous 回避）
    def _to_float_or_none(v) -> Optional[float]:
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