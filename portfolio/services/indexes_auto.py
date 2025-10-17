# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, datetime
from typing import Dict, Any
import yfinance as yf
from django.conf import settings

# ===================== 設定 =====================
INDEX_SYMBOLS = {
    # --- 国内 ---
    "TOPIX": "^TOPX",
    "N225": "^N225",
    "JPX400": "1319.T",
    "MOTHERS": "2516.T",
    "REIT": "1343.T",
    # --- 海外 ---
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "DAX": "^GDAXI",
    "FTSE": "^FTSE",
    "HSI": "^HSI",
    # --- 為替 ---
    "USDJPY": "USDJPY=X",
    "EURJPY": "EURJPY=X",
    # --- コモディティ ---
    "WTI": "CL=F",
    "GOLD": "GC=F",
    "COPPER": "HG=F",
}

# ===================== ヘルパ =====================
def _safe_float(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0

def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _market_dir() -> str:
    return os.path.join(_media_root(), "market")

# ===================== 主要指数の自動取得 =====================
def fetch_index_rs(days: int = 20) -> Dict[str, Dict[str, Any]]:
    """
    各指数の1日・5日・20日リターンと出来高比を算出し、
    indexes_YYYY-MM-DD.json に保存。
    """
    today = datetime.date.today()
    end = today
    start = today - datetime.timedelta(days=days * 3)

    out = {"date": today.isoformat(), "data": []}

    for name, symbol in INDEX_SYMBOLS.items():
        try:
            df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
            if len(df) < 21:
                continue

            close = df["Close"]
            vol = df["Volume"] if "Volume" in df.columns else None

            ret_1d = (close.iloc[-1] / close.iloc[-2] - 1) * 100
            ret_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100
            ret_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100
            vol_ratio = 1.0
            if vol is not None and len(vol) > 20:
                vol_ratio = _safe_float(vol.iloc[-1]) / max(1.0, vol.rolling(20).mean().iloc[-1])

            out["data"].append({
                "symbol": name,
                "ret_1d": round(ret_1d, 2),
                "ret_5d": round(ret_5d, 2),
                "ret_20d": round(ret_20d, 2),
                "vol_ratio": round(vol_ratio, 2),
            })
        except Exception as e:
            print(f"[WARN] {name} failed: {e}")

    # 保存
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)
    jpath = os.path.join(mdir, f"indexes_{today.isoformat()}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote: {jpath} ({len(out['data'])} symbols)")

    return out