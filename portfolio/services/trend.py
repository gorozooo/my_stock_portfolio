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

# ベンチマーク（RS計算用）
_INDEX_TICKER = os.environ.get("INDEX_TICKER", "^N225")  # 例: ^N225, ^GSPC など

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
# =========================================================
def _load_tse_map_if_needed() -> None:
    """tse_list.json / tse_list.csv を読み込み、_TSE_MAP = {CODE: 日本語名} を作る。
       - JSON は list[{"code","name"}] も dict{"7011":"三菱重工業"} も許容
       - CSV は header に code,name があればOK
    """
    global _TSE_MAP, _TSE_MTIME

    json_m = os.path.getmtime(_TSE_JSON_PATH) if os.path.isfile(_TSE_JSON_PATH) else 0.0
    csv_m  = os.path.getmtime(_TSE_CSV_PATH)  if os.path.isfile(_TSE_CSV_PATH)  else 0.0

    # 変更がなければキャッシュを使う
    if not _TSE_ALWAYS_RELOAD and _TSE_MAP and _TSE_MTIME == (json_m, csv_m):
        return

    df = None

    # ---------- JSON 優先 ----------
    if os.path.isfile(_TSE_JSON_PATH):
        try:
            with open(_TSE_JSON_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)

            if isinstance(raw, list):
                # 例: [{"code":"7011","name":"三菱重工業"}, ...]
                d = pd.DataFrame(raw)
            elif isinstance(raw, dict):
                # 例: {"7011":"三菱重工業", ...}
                d = pd.DataFrame([{"code": k, "name": v} for k, v in raw.items()])
            else:
                raise ValueError("tse_list.json: unexpected root type")

            cols = {c.lower(): c for c in d.columns}
            code = cols.get("code") or cols.get("ticker") or cols.get("symbol")
            name = cols.get("name") or cols.get("jp_name") or cols.get("company")
            if not (code and name):
                raise ValueError("tse_list.json must have 'code' and 'name' columns")

            d = d[[code, name]].rename(columns={code: "code", name: "name"})
            df = d
            _d(f"loaded json ({len(df)} rows)")
        except Exception as e:
            _d(f"failed to load json: {e}")

    # ---------- CSV フォールバック ----------
    if df is None and os.path.isfile(_TSE_CSV_PATH):
        try:
            d = pd.read_csv(_TSE_CSV_PATH, encoding="utf-8-sig", dtype=str)
            d = d.rename(columns={c: c.lower() for c in d.columns})
            if not {"code", "name"}.issubset(d.columns):
                raise ValueError("tse_list.csv needs 'code' and 'name'")
            df = d[["code", "name"]]
            _d(f"loaded csv ({len(df)} rows)")
        except Exception as e:
            _d(f"failed to load csv: {e}")

    # ---------- どちらも無い/不正 ----------
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

def _lookup_name_jp_from_list(ticker: str) -> Optional[str]:
    _load_tse_map_if_needed()
    if not _TSE_MAP or not ticker:
        return None
    head = ticker.upper().split(".", 1)[0]
    name = _TSE_MAP.get(head)
    if _TSE_DEBUG:
        _d(f"lookup {head} -> {repr(name)}")
    return name

# =========================================================
# ティッカー正規化 / 名前取得
# =========================================================
_JP_ALNUM = re.compile(r"^[0-9A-Z]{4,5}$")

def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    if _JP_ALNUM.match(t):
        return f"{t}.T"
    return t

def _fetch_name_prefer_jp(ticker: str) -> str:
    name = _lookup_name_jp_from_list(ticker)
    if isinstance(name, str) and name.strip():
        return name.strip()
    try:
        info = getattr(yf.Ticker(str(ticker)), "info", {}) or {}
        name = info.get("shortName") or info.get("longName") or info.get("name")
        if isinstance(name, str) and name.strip():
            return _clean_text(name)
    except Exception:
        pass
    head = ticker.upper().split(".", 1)[0]
    return head or ticker

# =========================================================
# テクニカル（ADX/ATRなど）
# =========================================================
def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr

def _wilder_smooth(series: pd.Series, n: int) -> pd.Series:
    s = series.copy()
    out = pd.Series(index=s.index, dtype=float)
    out.iloc[n-1] = s.iloc[:n].mean()
    for i in range(n, len(s)):
        out.iloc[i] = (out.iloc[i-1] * (n - 1) + s.iloc[i]) / n
    return out

def _adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> Optional[float]:
    try:
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        tr = _true_range(high, low, close)
        atr = _wilder_smooth(tr, n)

        plus_di = 100 * _wilder_smooth(plus_dm, n) / atr
        minus_di = 100 * _wilder_smooth(minus_dm, n) / atr
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di)) * 100
        adx = _wilder_smooth(dx, n)
        v = float(adx.dropna().iloc[-1])
        return v
    except Exception:
        return None

def _annualized_vol(p: pd.Series, win: int) -> Optional[float]:
    try:
        r = p.pct_change().dropna()
        if len(r) < max(10, win):
            return None
        return float(r.tail(win).std() * np.sqrt(252) * 100.0)
    except Exception:
        return None

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> Optional[float]:
    try:
        tr = _true_range(high, low, close)
        atr = _wilder_smooth(tr, n)
        return float(atr.dropna().iloc[-1])
    except Exception:
        return None

# ---------- yfinance DF から特定フィールドの Series を安全に取り出す ----------
def _pick_field(df: pd.DataFrame, field: str) -> pd.Series:
    """
    field: 'Close' | 'High' | 'Low' | 'Volume'
    - 単一列: その列を返す
    - MultiIndex: level=0 に field があれば xs で取り出し、最初の列を使う
    """
    if isinstance(df.columns, pd.MultiIndex):
        if field in df.columns.get_level_values(0):
            obj = df.xs(field, axis=1, level=0, drop_level=True)
            s = obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj
            return pd.to_numeric(s, errors="coerce")
        # 想定外なら最後の列を使う（数値化）
        return pd.to_numeric(df.iloc[:, -1], errors="coerce")
    # 通常の単一Index
    # 列名比較は文字列化して大文字小文字無視
    col = None
    for c in df.columns:
        if str(c).lower() == field.lower():
            col = c
            break
    if col is None:
        raise ValueError(f"{field} column not found")
    return pd.to_numeric(df[col], errors="coerce")

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
    # 追加
    ma20: Optional[float]
    ma50: Optional[float]
    ma200: Optional[float]
    ma_order: Optional[str]          # "20>50>200" 等
    adx14: Optional[float]
    rs_6m_pct: Optional[float]       # 6か月ベンチ超過(%)
    hi52_gap_pct: Optional[float]    # 52週高値までの距離(%) 正=未到達
    lo52_gap_pct: Optional[float]    # 52週安値からの距離(%) 正=上
    vol20_ann_pct: Optional[float]
    vol60_ann_pct: Optional[float]
    atr14: Optional[float]
    adv20: Optional[float]           # 20日売買代金平均（Close*Volume）

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
    ticker = _normalize_ticker(str(ticker))
    if not ticker:
        raise ValueError("ticker is required")

    # いろいろ計算するので長めに取る（52週=~252営業日）
    period_days = max(days + 300, 420)
    df = yf.download(ticker, period=f"{period_days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("価格データを取得できませんでした")

    # --- Close/High/Low/Volume を安全に抽出 ---
    close_s = _pick_field(df, "Close").dropna()
    high_s  = _pick_field(df, "High").dropna()
    low_s   = _pick_field(df, "Low").dropna()
    vol_s   = _pick_field(df, "Volume").dropna()

    if close_s.empty:
        raise ValueError("終値データが空でした")

    s_recent = close_s.tail(days)
    if len(s_recent) < max(15, ma_long_win):
        raise ValueError(f"データ日数が不足しています（取得: {len(s_recent)}日）")

    # ------- 基本MA -------
    ma_short = s_recent.rolling(ma_short_win).mean()
    ma_long  = s_recent.rolling(ma_long_win).mean()
    ma_s = _to_float_or_none(ma_short.iloc[[-1]])
    ma_l = _to_float_or_none(ma_long.iloc[[-1]])

    # ------- 回帰傾き -------
    y = s_recent.values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _b = np.polyfit(x, y, 1)
    last_price = y[-1]
    slope_daily_pct = (k / last_price) * 100.0 if last_price else 0.0
    slope_ann_pct = slope_daily_pct * 252.0

    # ------- シグナル -------
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

    # ------- 追加指標（過去長期データ使用） -------
    ma20 = float(close_s.tail(20).mean())  if len(close_s) >= 20  else None
    ma50 = float(close_s.tail(50).mean())  if len(close_s) >= 50  else None
    ma200= float(close_s.tail(200).mean()) if len(close_s) >= 200 else None

    def _ma_order_str(a,b,c):
        if None in (a,b,c):
            return None
        order = sorted([("20",a),("50",b),("200",c)], key=lambda t: t[1], reverse=True)
        return ">".join([t[0] for t in order])  # e.g. "20>50>200"

    ma_order = _ma_order_str(ma20, ma50, ma200)

    # ADX/ATR（長期列で計算）
    # 高値/安値/終値のインデックスを合わせる
    common_idx = close_s.index.intersection(high_s.index).intersection(low_s.index)
    adx14 = _adx(high_s.reindex(common_idx), low_s.reindex(common_idx), close_s.reindex(common_idx), n=14)
    atr14 = _atr(high_s.reindex(common_idx), low_s.reindex(common_idx), close_s.reindex(common_idx), n=14)

    # 年化ボラ
    vol20 = _annualized_vol(close_s, 20)
    vol60 = _annualized_vol(close_s, 60)

    # 52週高安
    if len(close_s) >= 252:
        hi52 = float(close_s.tail(252).max())
        lo52 = float(close_s.tail(252).min())
        last = float(close_s.iloc[-1])
        hi52_gap = (hi52 - last) / last * 100.0
        lo52_gap = (last - lo52) / lo52 * 100.0
    else:
        hi52_gap = lo52_gap = None

    # RS(6M) ベンチ比
    rs_6m = None
    try:
        bench = yf.download(_INDEX_TICKER, period="300d", interval="1d", progress=False)
        if bench is not None and not bench.empty:
            bclose = _pick_field(bench, "Close").dropna()
            # 130営業日 ≒ 6か月
            r_stock = close_s.pct_change().dropna().tail(130)
            r_bench = bclose.pct_change().dropna().tail(130)
            joined = pd.concat([r_stock, r_bench], axis=1, join="inner")
            joined.columns = ["s","b"]
            if not joined.empty:
                cum_s = (1 + joined["s"]).prod() - 1.0
                cum_b = (1 + joined["b"]).prod() - 1.0
                rs_6m = (cum_s - cum_b) * 100.0
    except Exception:
        rs_6m = None

    # ADV20（売買代金20日平均 = Close * Volume）
    adv20 = None
    try:
        vol_aligned = vol_s.reindex(close_s.index)
        adv20 = float((vol_aligned.tail(20) * close_s.tail(20)).mean())
    except Exception:
        pass

    asof = s_recent.index[-1].date().isoformat()
    name = _fetch_name_prefer_jp(ticker)

    return TrendResult(
        ticker=ticker,
        name=name,
        asof=asof,
        days=int(len(s_recent)),
        signal=signal,
        reason=reason,
        slope=float(k),
        slope_annualized_pct=float(slope_ann_pct),
        ma_short=ma_s,
        ma_long=ma_l,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        ma_order=ma_order,
        adx14=adx14,
        rs_6m_pct=rs_6m,
        hi52_gap_pct=hi52_gap,
        lo52_gap_pct=lo52_gap,
        vol20_ann_pct=vol20,
        vol60_ann_pct=vol60,
        atr14=atr14,
        adv20=adv20,
    )