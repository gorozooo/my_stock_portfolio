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
# 設定（環境変数で上書き可）
# =====================================================================

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))

_TSE_CSV_PATH = os.environ.get(
    "TSE_CSV_PATH",
    os.path.join(_BASE_DIR, "data", "tse_list.csv"),
)
_TSE_JSON_PATH = os.environ.get(
    "TSE_JSON_PATH",
    os.path.join(_BASE_DIR, "data", "tse_list.json"),
)
_TSE_ALWAYS_RELOAD = os.environ.get("TSE_CSV_ALWAYS_RELOAD") == "1"
_TSE_DEBUG = os.environ.get("TSE_DEBUG") == "1"

# モジュール内キャッシュ
_TSE_MAP: Dict[str, str] = {}
_TSE_SRC_TAG: str = ""  # "csv" or "json" or ""


# =====================================================================
# 文字クリーン
# =====================================================================

def _clean_text(s: str) -> str:
    """
    不可視文字や私用領域の文字などを削除し、Unicode正規化(NFKC)して返す。
    Excel→CSV/JSONで混入する「」(PUA: U+E000–U+F8FF) 等にも対応。
    """
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFKC", s)
    # zero width & BOM
    s = re.sub(r"[\u200B-\u200D\uFEFF]", "", s)
    # variation selectors
    s = re.sub(r"[\uFE00-\uFE0F]", "", s)
    # control chars + DEL
    s = re.sub(r"[\u0000-\u001F\u007F]", "", s)
    # Private Use Area（“”等）
    s = re.sub(r"[\uE000-\uF8FF]", "", s)
    # 全角スペース→半角
    s = s.replace("\u3000", " ")
    # 余分な空白を整形
    s = re.sub(r"\s+", " ", s).strip()
    return s


# =====================================================================
# 東証リスト ロード（CSV/JSON どちらでも可）
# =====================================================================

def _load_tse_map_if_needed() -> None:
    """
    CSV/JSON のどちらかから code->name マップをロード。
    - CSV が存在すれば CSV を優先
    - 無ければ JSON を見る
    どちらの経路でも読み込み時に _clean_text を通す
    """
    global _TSE_MAP, _TSE_SRC_TAG

    if _TSE_MAP and not _TSE_ALWAYS_RELOAD:
        return

    # まず CSV を試す
    if os.path.isfile(_TSE_CSV_PATH):
        try:
            df = pd.read_csv(
                _TSE_CSV_PATH,
                encoding="utf-8-sig",
                dtype={"code": str, "name": str},
            )
            cols = {c.lower(): c for c in df.columns}
            code_col = cols.get("code")
            name_col = cols.get("name")
            if not code_col or not name_col:
                raise ValueError("CSV に 'code' と 'name' 列が必要です")

            df[code_col] = df[code_col].astype(str).map(_clean_text)
            df[name_col] = df[name_col].astype(str).map(_clean_text)

            _TSE_MAP = {
                row[code_col]: row[name_col]
                for _, row in df.iterrows()
                if row[code_col] and row[name_col]
            }
            _TSE_SRC_TAG = "csv"
            if _TSE_DEBUG:
                print(f"[TSE] loaded csv ({len(_TSE_MAP)} rows)")
            return
        except Exception as e:
            _TSE_MAP = {}
            _TSE_SRC_TAG = ""
            if _TSE_DEBUG:
                print("[TSE] CSV load failed:", e)

    # CSV が駄目なら JSON
    if os.path.isfile(_TSE_JSON_PATH):
        try:
            with open(_TSE_JSON_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)

            if isinstance(raw, dict):
                m = {str(k).strip(): _clean_text(str(v)) for k, v in raw.items()}
            elif isinstance(raw, list):
                # [{"code": "...", "name": "..."}] 形式も許容
                m = {}
                for d in raw:
                    code = _clean_text(str(d.get("code", "")).strip())
                    name = _clean_text(str(d.get("name", "")).strip())
                    if code and name:
                        m[code] = name
            else:
                m = {}

            _TSE_MAP = m
            _TSE_SRC_TAG = "json"
            if _TSE_DEBUG:
                print(f"[TSE] loaded json ({len(_TSE_MAP)} rows)")
            return
        except Exception as e:
            _TSE_MAP = {}
            _TSE_SRC_TAG = ""
            if _TSE_DEBUG:
                print("[TSE] JSON load failed:", e)

    # どちらも無し/失敗
    _TSE_MAP = {}
    _TSE_SRC_TAG = ""
    if _TSE_DEBUG:
        print("[TSE] no list loaded")


def _lookup_name_jp_from_list(ticker: str) -> Optional[str]:
    """
    ティッカーが東証（nnnn / nnnnn / nnnn.T など）なら CSV/JSON マップから日本語名を返す。
    見つからなければ None。
    """
    _load_tse_map_if_needed()
    if not _TSE_MAP:
        return None

    t = (ticker or "").upper().strip()
    if not t:
        return None

    # "7203" / "7203.T" / "7203.TK" などの数字部分を抜く
    numeric = t.split(".", 1)[0]
    if numeric.isdigit() and len(numeric) in (4, 5):
        name = _TSE_MAP.get(numeric)
        if name:
            name = _clean_text(name)
            if _TSE_DEBUG:
                print(f"[TSE] lookup {numeric} -> '{name}' (src={_TSE_SRC_TAG})")
            return name
        if _TSE_DEBUG:
            print(f"[TSE] lookup {numeric} -> None")
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
    if "." in t:
        return t
    if t.isdigit() and len(t) in (4, 5):
        return f"{t}.T"
    return t


def _fetch_name_prefer_jp(ticker: str) -> str:
    """
    優先順:
      1) 東証リスト（CSV/JSON）の日本語名
      2) 日本株コードなら数字だけ（英語へは落とさない）
      3) 海外銘柄等は yfinance の名称（shortName/longName/name）
      4) 何も無ければティッカー
    """
    # まず東証リスト
    name_csv = _lookup_name_jp_from_list(ticker)
    if name_csv:
        return _clean_text(name_csv)

    # 日本株コードなら数字だけを返す（余計な“”などが残らない）
    t = (ticker or "").upper().strip()
    numeric = t.split(".", 1)[0]
    if numeric.isdigit() and len(numeric) in (4, 5):
        return numeric

    # 海外銘柄などは yfinance
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
    name: str                   # 日本語名を想定（無ければ英語 or ティッカー/数字）
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

    # データ取得（市場休日も考慮して余裕を持って長めに）
    period_days = max(days + 30, 120)
    # yfinance の FutureWarning 対策で auto_adjust を明示
    df = yf.download(
        ticker,
        period=f"{period_days}d",
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
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

    def _to_float_or_none(v) -> Optional[float]:
        try:
            if pd.isna(v):
                return None
        except Exception:
            pass
        try:
            return float(v)
        except Exception:
            # pandas Series を誤って渡された場合などの保険
            try:
                return float(getattr(v, "iloc", [None])[-1])
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
    # 最終的にもクリーン（ダブルセーフ）
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