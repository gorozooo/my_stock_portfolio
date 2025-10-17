# portfolio/services/indexes_auto.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, datetime
from typing import Dict, Any, Optional

import yfinance as yf
import pandas as pd
from django.conf import settings

# ===================== 設定 =====================
# できるだけ出来高が入るETF/代替シンボルを使用（指数 ^～ の代替）
INDEX_SYMBOLS: Dict[str, str] = {
    # --- 国内（ETF代替） ---
    "TOPIX": "1306.T",      # TOPIX連動型上場投信
    "N225": "1321.T",       # 日経225連動型上場投信
    "JPX400": "1591.T",     # JPX日経400
    "MOTHERS": "2516.T",    # マザーズ（代替ETF。無ければ自動スキップ）
    "REIT": "1343.T",       # 東証REIT指数

    # --- 海外（ETF代替） ---
    "SPX": "SPY",           # S&P500
    "NDX": "QQQ",           # NASDAQ100
    "DAX": "EWG",           # ドイツ大型株 ETF
    "FTSE": "EWU",          # 英国株 ETF
    "HSI": "EWH",           # 香港株 ETF

    # --- 為替 ---
    "USDJPY": "USDJPY=X",
    "EURJPY": "EURJPY=X",

    # --- コモディティ（先物連動） ---
    "WTI": "CL=F",
    "GOLD": "GC=F",
    "COPPER": "HG=F",
}

# ===================== ヘルパ =====================
def _media_root() -> str:
    """
    MEDIA_ROOT が未設定なら プロジェクトCWD配下の 'media' を使う。
    """
    mr = getattr(settings, "MEDIA_ROOT", "") or ""
    if mr:
        return mr
    return os.path.join(os.getcwd(), "media")

def _market_dir() -> str:
    return os.path.join(_media_root(), "market")

def _to_float(x: Any, default: float = 0.0) -> float:
    """
    pandas / numpy を安全に float 化。
    - Series/Index/ndarray は末尾要素を取り出して float
    - NaN や例外は default
    """
    try:
        if x is None:
            return default
        if isinstance(x, (list, tuple)) and x:
            return float(x[-1])
        if hasattr(x, "iloc"):
            # pandas Series / Index
            if len(x) == 0:
                return default
            return float(x.iloc[-1])
        # numpy scalar の item() 対応
        if hasattr(x, "item"):
            return float(x.item())
        return float(x)
    except Exception:
        return default

def _pct_return(series: pd.Series, periods: int) -> Optional[float]:
    """
    series の後ろから periods 戻った値と直近値で %リターン（100倍）を計算。
    データ不足なら None。
    """
    try:
        if not isinstance(series, pd.Series):
            return None
        if len(series) <= periods:
            return None
        latest = _to_float(series.iloc[-1])
        past = _to_float(series.iloc[-(periods+1)])
        if past == 0:
            return None
        return (latest / past - 1.0) * 100.0
    except Exception:
        return None

def _vol_ratio(volume: Optional[pd.Series], window: int = 20) -> Optional[float]:
    """
    直近出来高 / 直近window日移動平均出来高。出来高列が無い場合やデータ不足は None。
    """
    try:
        if volume is None or not isinstance(volume, pd.Series) or len(volume) < window:
            return None
        ma = volume.rolling(window).mean()
        v_last = _to_float(volume.iloc[-1])
        v_ma = _to_float(ma.iloc[-1])
        if v_ma <= 0:
            return None
        return v_last / v_ma
    except Exception:
        return None

# ===================== 主要指数の自動取得 =====================
def fetch_index_rs(days: int = 20) -> Dict[str, Dict[str, Any]]:
    """
    各指数の1日・5日・20日リターンと出来高比を算出し、market/indexes_YYYY-MM-DD.json に保存。
    - 代替シンボルを順にフォールバック
    - データが6本未満でも“取れた期間だけ”で計算（r1/r5だけ等）
    - 出来高が無い/短い場合は vol_ratio=None で続行
    - スキップ理由を標準出力に出す
    """
    import datetime
    import yfinance as yf
    import pandas as pd

    # 代替候補を複数用意（先に定義してある INDEX_SYMBOLS を使いつつ、別候補も足す）
    ALIASES: Dict[str, list] = {
        "TOPIX":  ["1306.T", "1305.T"],
        "N225":   ["1321.T", "1330.T"],
        "JPX400": ["1591.T"],
        "MOTHERS":["2516.T"],
        "REIT":   ["1343.T"],
        "SPX":    ["SPY", "IVV", "^GSPC"],
        "NDX":    ["QQQ", "^NDX"],
        "DAX":    ["EWG", "^GDAXI"],
        "FTSE":   ["EWU", "^FTSE"],
        "HSI":    ["EWH", "^HSI"],
        "USDJPY": ["USDJPY=X"],
        "EURJPY": ["EURJPY=X"],
        "WTI":    ["CL=F"],
        "GOLD":   ["GC=F", "GLD"],
        "COPPER": ["HG=F"],
    }

    # 最低限 20日を見るが、取得は余裕を持って（ネットが弱い時の穴埋め）
    period_days = max(90, days * 5)
    period_str = f"{period_days}d"

    today = __import__("datetime").date.today()
    out = {"date": today.isoformat(), "data": []}

    # log helper
    def _log(msg: str):
        print(msg)

    for name in ALIASES.keys():
        syms = ALIASES[name]
        got = False

        for symbol in syms:
            try:
                df = yf.download(
                    symbol,
                    period=period_str,
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    threads=False,
                )
                if df is None or len(df) == 0:
                    _log(f"[SKIP] {name}:{symbol} → no data")
                    continue

                # Close は必須
                if "Close" not in df.columns:
                    _log(f"[SKIP] {name}:{symbol} → Close column missing")
                    continue

                close: pd.Series = df["Close"].dropna()
                volume = df["Volume"].dropna() if "Volume" in df.columns else None

                if len(close) < 2:
                    _log(f"[SKIP] {name}:{symbol} → too short ({len(close)})")
                    continue

                # 取れた範囲で計算（足りないものは None）
                r1 = _pct_return(close, 1)
                r5 = _pct_return(close, 5) if len(close) > 5 else None
                r20 = _pct_return(close, 20) if len(close) > 21 else None
                vr = _vol_ratio(volume, 20) if volume is not None else None

                if r1 is None and r5 is None and r20 is None and vr is None:
                    _log(f"[SKIP] {name}:{symbol} → all metrics None")
                    continue

                out["data"].append({
                    "symbol": name,
                    "ret_1d": None if r1 is None else round(r1, 2),
                    "ret_5d": None if r5 is None else round(r5, 2),
                    "ret_20d": None if r20 is None else round(r20, 2),
                    "vol_ratio": None if vr is None else round(vr, 2),
                })
                _log(f"[OK] {name}:{symbol} rows={len(close)} r1={r1 if r1 is not None else 'NA'} r5={r5 if r5 is not None else 'NA'} r20={r20 if r20 is not None else 'NA'}")
                got = True
                break  # この指数は成功したので他の候補は不要

            except Exception as e:
                _log(f"[WARN] {name}:{symbol} failed: {e}")
                continue

        if not got:
            _log(f"[MISS] {name} → 全候補NG（今回は見送り）")

    # 保存
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)
    jpath = os.path.join(mdir, f"indexes_{today.isoformat()}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote: {jpath} ({len(out['data'])} symbols)")
    return out