# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, datetime
from typing import Dict, Any, Optional

import yfinance as yf
import pandas as pd
from django.conf import settings

# ===================== 設定 =====================
INDEX_SYMBOLS: Dict[str, str] = {
    "TOPIX": "1306.T",
    "N225": "1321.T",
    "JPX400": "1591.T",
    "MOTHERS": "2516.T",
    "REIT": "1343.T",
    "SPX": "SPY",
    "NDX": "QQQ",
    "DAX": "EWG",
    "FTSE": "EWU",
    "HSI": "EWH",
    "USDJPY": "USDJPY=X",
    "EURJPY": "EURJPY=X",
    "WTI": "CL=F",
    "GOLD": "GC=F",
    "COPPER": "HG=F",
}

# ===================== ヘルパ =====================
def _media_root() -> str:
    mr = getattr(settings, "MEDIA_ROOT", "") or ""
    if mr:
        return mr
    return os.path.join(os.getcwd(), "media")

def _market_dir() -> str:
    return os.path.join(_media_root(), "market")

def _to_float(x: Any, default: float = 0.0) -> float:
    """
    将来の Pandas 仕様変更 (float(Series) 廃止予定) に対応した安全変換。
    - Series → 最後の値を取得
    - NaN / None → default
    - numpy scalar / list / tuple にも対応
    """
    try:
        if x is None:
            return default
        if isinstance(x, pd.Series):
            if len(x) == 0:
                return default
            return float(x.iloc[-1])
        if isinstance(x, (list, tuple)) and len(x) > 0:
            return float(x[-1])
        if hasattr(x, "item"):
            return float(x.item())
        val = float(x)
        if pd.isna(val):
            return default
        return val
    except Exception:
        return default

def _pct_return(series: pd.Series, periods: int) -> Optional[float]:
    try:
        if not isinstance(series, pd.Series) or len(series) <= periods:
            return None
        latest = _to_float(series.iloc[-1])
        past = _to_float(series.iloc[-(periods + 1)])
        if past == 0:
            return None
        return (latest / past - 1.0) * 100.0
    except Exception:
        return None

def _vol_ratio(volume: Optional[pd.Series], window: int = 20) -> Optional[float]:
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

def _latest_file(pattern: str) -> Optional[str]:
    import glob
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)

# ===================== 主要指数の自動取得 =====================
def fetch_index_rs(days: int = 20) -> Dict[str, Dict[str, Any]]:
    """
    主要指数の1d/5d/20dリターンと出来高比を作成して media/market/indexes_YYYY-MM-DD.json へ保存。
    - yfinanceが使える場合 → 実データ取得
    - 取れない場合 → 手動ファイル / 過去履歴 / ダミーデータでフォールバック
    """
    from datetime import date
    today = date.today().isoformat()
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)
    out_path = os.path.join(mdir, f"indexes_{today}.json")
    out: Dict[str, Any] = {"date": today, "data": []}

    def _log(msg: str):
        print(msg)

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

    # --- 通信確認（SPYでテスト） ---
    online_ok = True
    try:
        test = yf.download("SPY", period="3d", interval="1d", progress=False, threads=False)
        online_ok = bool(test is not None and len(test) >= 2 and "Close" in test.columns)
    except Exception:
        online_ok = False

    # ========== オンライン ==========
    if online_ok:
        period_days = max(90, days * 5)
        period_str = f"{period_days}d"

        for name, syms in ALIASES.items():
            got = False
            for symbol in syms:
                try:
                    df = yf.download(symbol, period=period_str, interval="1d",
                                     auto_adjust=True, progress=False, threads=False)
                    if df is None or len(df) < 2 or "Close" not in df.columns:
                        _log(f"[SKIP] {name}:{symbol} → no/short data")
                        continue
                    close = df["Close"].dropna()
                    volume = df["Volume"].dropna() if "Volume" in df.columns else None

                    r1 = _pct_return(close, 1)
                    r5 = _pct_return(close, 5)
                    r20 = _pct_return(close, 20)
                    vr = _vol_ratio(volume, 20)

                    # 出来高がなくても価格リターンが取れれば採用
                    if all(v is None for v in [r1, r5, r20]):
                        _log(f"[SKIP] {name}:{symbol} → price metrics None")
                        continue

                    out["data"].append({
                        "symbol": name,
                        "ret_1d": None if r1 is None else round(r1, 2),
                        "ret_5d": None if r5 is None else round(r5, 2),
                        "ret_20d": None if r20 is None else round(r20, 2),
                        "vol_ratio": None if vr is None else round(vr, 2),
                    })
                    _log(f"[OK] {name}:{symbol}")
                    got = True
                    break
                except Exception as e:
                    _log(f"[WARN] {name}:{symbol} failed: {e}")
            if not got:
                _log(f"[MISS] {name} → 全候補NG（今回は見送り）")

        # 保存
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"Wrote: {out_path} ({len(out['data'])} symbols)")
        return out

    # ========== オフライン・フォールバック ==========
    _log("[OFFLINE] yfinance取得不可 → 手動/履歴/ダミーにフォールバック")

    manual_path = os.path.join(mdir, "indexes_manual.json")
    if os.path.exists(manual_path):
        try:
            with open(manual_path, "r", encoding="utf-8") as f:
                manual = json.load(f)
            if isinstance(manual, dict) and isinstance(manual.get("data"), list):
                out["data"] = manual["data"]
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
                print(f"[OFFLINE] used manual → {out_path} ({len(out['data'])} symbols)")
                return out
        except Exception as e:
            _log(f"[WARN] manual read failed: {e}")

    last_hist = _latest_file(os.path.join(mdir, "indexes_*.json"))
    if last_hist and os.path.exists(last_hist):
        try:
            with open(last_hist, "r", encoding="utf-8") as f:
                prev = json.load(f)
            if isinstance(prev, dict) and isinstance(prev.get("data"), list) and len(prev["data"]) > 0:
                out["data"] = prev["data"]
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
                print(f"[OFFLINE] copied from {os.path.basename(last_hist)} → {out_path} ({len(out['data'])} symbols)")
                return out
        except Exception as e:
            _log(f"[WARN] history read failed: {e}")

    # ダミーデータ
    synth = [
        {"symbol": "TOPIX", "ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0},
        {"symbol": "N225", "ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0},
        {"symbol": "SPX", "ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0},
        {"symbol": "NDX", "ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": 1.0},
        {"symbol": "USDJPY", "ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": None},
        {"symbol": "WTI", "ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": None},
        {"symbol": "GOLD", "ret_1d": 0.0, "ret_5d": 0.0, "ret_20d": 0.0, "vol_ratio": None},
    ]
    out["data"] = synth
    print("[SYNTH] generated neutral snapshot (7 symbols)")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote: {out_path} ({len(out['data'])} symbols)")
    return out