from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict
import os
import re
import unicodedata

import numpy as np
import pandas as pd
import yfinance as yf


# =====================================================================
# 日本語銘柄名 CSV ローダ（data/tse_list.csv を想定）
# =====================================================================

# CSV の既定パス（必要なら環境変数で上書き）
_TSE_CSV_PATH = os.environ.get(
    "TSE_CSV_PATH",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tse_list.csv"),
)

# モジュール内キャッシュ
_TSE_MAP: Dict[str, str] = {}
_TSE_CSV_MTIME: float = 0.0


def _clean_text(s: object) -> str:
    """
    テキストのクリーニング:
    - str化 → NFKC 正規化（全角英数の統一等）
    - BOM/制御文字/ゼロ幅/双方向制御/私用領域等の不可視文字を除去
    - 前後の空白を除去
    """
    if s is None:
        return ""
    t = str(s)

    # Unicode 正規化（全角英数→半角など）
    t = unicodedata.normalize("NFKC", t)

    # 制御文字（C0/C1）と DEL
    t = re.sub(r"[\u0000-\u001F\u007F-\u009F]", "", t)
    # ゼロ幅/双方向制御/ワード結合子等
    t = re.sub(r"[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF]", "", t)
    # 私用領域（PUA）
    t = re.sub(r"[\uE000-\uF8FF]", "", t)

    return t.strip()


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
            dtype=str,  # 何が来ても文字列として読む
        )

        # 列名のゆらぎに対応（大小文字/全角半角）
        cols = {unicodedata.normalize("NFKC", c).strip().lower(): c for c in df.columns}
        code_col = cols.get("code")
        name_col = cols.get("name")
        if not code_col or not name_col:
            # 代表的な日本語列名にもフォールバック（念のため）
            code_col = code_col or cols.get("銘柄コード") or cols.get("コード") or "code"
            name_col = name_col or cols.get("銘柄名") or cols.get("名称") or "name"
            # 無ければ KeyError を起こさないよう存在しない場合は空 DataFrame へ
            for need in (code_col, name_col):
                if need not in df.columns:
                    df[need] = ""

        # 値のクリーニング
        df[code_col] = df[code_col].map(_clean_text)
        df[name_col] = df[name_col].map(_clean_text)

        # コード列は数字以外を全除去（"8306.0" や空白混入・PUA混入を吸収）
        df[code_col] = df[code_col].str.replace(r"\D", "", regex=True)

        # 4〜5桁のみ採用
        df = df[df[code_col].str.fullmatch(r"\d{4,5}")]

        # マップ化（例: "8306" -> "三菱UFJフィナンシャルグループ"）
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


def _code_from_ticker_like(t: str) -> Optional[str]:
    """
    '7203' / '7203.T' / '7203.TK' などから 4〜5桁の数字コードを抽出。
    マッチしなければ None。
    """
    if not t:
        return None
    head = t.split(".", 1)[0]
    code = re.sub(r"\D", "", head or "")
    return code if re.fullmatch(r"\d{4,5}", code) else None


def _lookup_name_jp_from_csv(ticker: str) -> Optional[str]:
    """
    ティッカーが東証（nnnn or nnnn.T 等）なら CSV から日本語名を返す。
    見つからなければ None。
    """
    _load_tse_map_if_needed()
    if not _TSE_MAP:
        return None
    code = _code_from_ticker_like((ticker or "").upper().strip())
    if not code:
        return None
    return _TSE_MAP.get(code)


# =====================================================================
# チッカー正規化 / 名前取得
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
    if "." in t:
        return t
    if re.fullmatch(r"\d{4,5}", re.sub(r"\D", "", t)):
        return f"{re.sub(r'\\D', '', t)}.T"
    return t


def _fetch_name_prefer_jp(ticker: str) -> str:
    """
    1) CSV（全銘柄日本語辞書）があれば最優先
    2) なければ yfinance から取得（shortName/longName/name）
    3) それも無ければティッカーを返す
    """
    name_csv = _lookup_name_jp_from_csv(ticker)
    if isinstance(name_csv, str) and name_csv.strip():
        return name_csv.strip()

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

    # NaN セーフに float/None 化
    def _to_float_or_none(v) -> Optional[float]:
        try:
            return None if pd.isna(v) else float(v)
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
        signal, reason = "UP", "回帰傾き(年率換算)が正で大きめ"
    elif slope_ann_pct <= -5.0:
        signal, reason = "DOWN", "回帰傾き(年率換算)が負で大きめ"

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