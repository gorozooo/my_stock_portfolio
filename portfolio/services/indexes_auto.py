# portfolio/services/indexes_auto.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from datetime import date, datetime
from typing import Dict, Any, Optional, List, Tuple

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

ALIASES: Dict[str, List[str]] = {
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

# ===================== ヘルパ =====================
def _media_root() -> str:
    mr = getattr(settings, "MEDIA_ROOT", "") or ""
    return mr or os.path.join(os.getcwd(), "media")

def _market_dir() -> str:
    return os.path.join(_media_root(), "market")

def _latest_file(pattern: str) -> Optional[str]:
    import glob
    paths = glob.glob(pattern)
    if not paths:
        return None

    def _pick_date(p: str) -> Tuple[int, str]:
        base = os.path.basename(p)
        try:
            dt_text = base.split("_", 1)[1].split(".", 1)[0]
            key = int(datetime.fromisoformat(dt_text).strftime("%Y%m%d"))
        except Exception:
            key = 0
        return (key, p)

    paths.sort(key=lambda x: _pick_date(x)[0])
    paths.sort(key=lambda x: os.path.getmtime(x))
    return paths[-1] if paths else None

def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, (list, tuple)) and x:
            return float(x[-1])
        if hasattr(x, "iloc"):
            if len(x) == 0:
                return default
            return float(x.iloc[-1])
        if hasattr(x, "item"):
            return float(x.item())
        return float(x)
    except Exception:
        return default

def _pct_return(series: pd.Series, periods: int) -> Optional[float]:
    try:
        if not isinstance(series, pd.Series):
            return None
        if len(series) <= periods:
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

def _log(msg: str) -> None:
    print(msg)

# ===================== 主要指数の自動取得 =====================
def fetch_index_rs(days: int = 20) -> Dict[str, Dict[str, Any]]:
    """
    主要指数の1d/5d/20dリターンと出来高比を作成して media/market/indexes_YYYY-MM-DD.json へ保存。
    取得手段を段階的に切替し、Close欠落時はAdj Close代用。全滅時は直近の非空履歴を転写。
    """
    today = date.today().isoformat()
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)
    out_path = os.path.join(mdir, f"indexes_{today}.json")
    out: Dict[str, Any] = {"date": today, "data": []}

    def _ensure_close(df: pd.DataFrame) -> Optional[pd.Series]:
        if df is None or len(df) == 0:
            return None
        if "Close" in df.columns:
            s = df["Close"].dropna()
            if len(s) > 0:
                return s
        if "Adj Close" in df.columns:
            s = df["Adj Close"].dropna()
            if len(s) > 0:
                return s
        return None

    def _try_download(symbol: str, period_days: int) -> Optional[pd.DataFrame]:
        """複数戦略で DataFrame を取りに行く（どれか成功すれば返す）"""
        period_strs = [f"{period_days}d", f"{max(period_days, 180)}d", "200d"]
        # 1) download(auto_adjust=True/False)
        for p in period_strs:
            try:
                df = yf.download(symbol, period=p, interval="1d",
                                 auto_adjust=True, progress=False, threads=False)
                if _ensure_close(df) is not None:
                    return df
            except Exception:
                pass
            try:
                df = yf.download(symbol, period=p, interval="1d",
                                 auto_adjust=False, progress=False, threads=False)
                if _ensure_close(df) is not None:
                    return df
            except Exception:
                pass
        # 2) Ticker.history
        try:
            tk = yf.Ticker(symbol)
            df = tk.history(period="200d", interval="1d", auto_adjust=True)
            if _ensure_close(df) is not None:
                return df
        except Exception:
            pass
        # 3) start/end（“今日含むとうまく返らない”ケースの回避）
        try:
            from datetime import timedelta
            end = date.today() - timedelta(days=1)
            start = end - timedelta(days=max(period_days, 200))
            df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                             interval="1d", auto_adjust=True, progress=False, threads=False)
            if _ensure_close(df) is not None:
                return df
        except Exception:
            pass
        return None

    # online check
    online_ok = True
    try:
        test = yf.download("SPY", period="3d", interval="1d", auto_adjust=True,
                           progress=False, threads=False)
        online_ok = bool(test is not None and len(test) >= 2 and _ensure_close(test) is not None)
    except Exception:
        online_ok = False

    if online_ok:
        period_days = max(120, days * 6)  # 休場・欠損に強め
        for name, syms in ALIASES.items():
            got = False
            for symbol in syms:
                df = _try_download(symbol, period_days)
                if df is None:
                    print(f"[SKIP] {name}:{symbol} → empty/invalid df")
                    continue

                close = _ensure_close(df)
                if close is None or len(close) < 2:
                    print(f"[SKIP] {name}:{symbol} → close too short")
                    continue

                volume = None
                if "Volume" in df.columns:
                    v = df["Volume"].dropna()
                    volume = v if len(v) > 0 else None

                # 有効本数に応じて段階的に算出
                n = len(close)
                r1 = _pct_return(close, 1) if n >= 2 else None
                r5 = _pct_return(close, 5) if n >= 6 else None
                r20 = _pct_return(close, 20) if n >= 21 else None
                vr = _vol_ratio(volume, 20) if volume is not None else None

                if r1 is None and r5 is None and r20 is None and vr is None:
                    print(f"[SKIP] {name}:{symbol} → all metrics None")
                    continue

                out["data"].append({
                    "symbol": name,
                    "ret_1d": None if r1 is None else round(r1, 2),
                    "ret_5d": None if r5 is None else round(r5, 2),
                    "ret_20d": None if r20 is None else round(r20, 2),
                    "vol_ratio": None if vr is None else round(vr, 2),
                })
                print(f"[OK] {name}:{symbol}")
                got = True
                break

            if not got:
                print(f"[MISS] {name} → 全候補NG（今回は見送り）")

        # 1件も取れなかった場合は、直近の非空ファイルを転写
        if len(out["data"]) == 0:
            last_hist = _latest_file(os.path.join(mdir, "indexes_*.json"))
            if last_hist:
                try:
                    with open(last_hist, "r", encoding="utf-8") as f:
                        prev = json.load(f)
                    if isinstance(prev, dict) and isinstance(prev.get("data"), list) and len(prev["data"]) > 0:
                        out["data"] = prev["data"]
                        print(f"[FALLBACK] copied from {os.path.basename(last_hist)}")
                except Exception:
                    pass

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"Wrote: {out_path} ({len(out['data'])} symbols)")
        return out

    # ====== オフライン（手動/履歴） ======
    print("[OFFLINE] yfinance 取得不可 → 手動/履歴にフォールバック")
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
            print(f"[WARN] manual read failed: {e}")

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
            print(f"[WARN] history read failed: {e}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[OFFLINE] no data available → wrote empty: {out_path} (0 symbols)")
    return out