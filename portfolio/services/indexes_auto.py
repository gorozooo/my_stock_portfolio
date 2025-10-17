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
    ・yfinanceの多段リトライ＆補完で収集
    ・収集ゼロ時は “自動” で履歴フォールバック → それも無ければニュートラル合成を保存（手動ファイル不要）
    """
    from datetime import date, timedelta
    import os, json
    import pandas as pd
    import yfinance as yf
    from glob import glob

    today = date.today().isoformat()
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)
    out_path = os.path.join(mdir, f"indexes_{today}.json")
    out: Dict[str, Any] = {"date": today, "data": []}

    # 候補シンボル（優先順）
    ALIASES: Dict[str, list] = {
        # --- 国内（ETF & 代替/指数） ---
        "TOPIX":  ["1306.T", "1305.T", "1473.T", "1475.T", "^TOPX"],
        "N225":   ["1321.T", "1330.T", "1329.T", "^N225"],
        "JPX400": ["1591.T", "1593.T"],
        "MOTHERS":["2516.T"],
        "REIT":   ["1343.T", "2555.T"],

        # --- 海外（ETF & 指数） ---
        "SPX":    ["SPY", "IVV", "^GSPC"],
        "NDX":    ["QQQ", "^NDX"],
        "DAX":    ["EWG", "^GDAXI"],
        "FTSE":   ["EWU", "^FTSE"],
        "HSI":    ["EWH", "^HSI"],

        # --- 為替 ---
        "USDJPY": ["USDJPY=X"],
        "EURJPY": ["EURJPY=X"],

        # --- コモディティ ---
        "WTI":    ["CL=F"],
        "GOLD":   ["GC=F", "GLD"],
        "COPPER": ["HG=F"],
    }

    # 最低限の“ニュートラル合成”対象（完全初回用）
    SYNTHETIC_MIN = ["TOPIX", "N225", "SPX", "NDX", "REIT", "USDJPY", "GOLD"]

    def _ensure_close(df: Optional[pd.DataFrame]) -> Optional[pd.Series]:
        if df is None or len(df) == 0:
            return None
        if "Close" in df.columns:
            s = pd.to_numeric(df["Close"], errors="coerce").ffill().dropna()
            if len(s) > 0:
                return s
        if "Adj Close" in df.columns:
            s = pd.to_numeric(df["Adj Close"], errors="coerce").ffill().dropna()
            if len(s) > 0:
                return s
        return None

    def _try_download(symbol: str, min_len: int) -> Optional[pd.DataFrame]:
        """多段リトライで DataFrame を取得。min_len は少なくとも 21(=20日リターン) を想定。"""
        # 1) download: auto_adjust True/False × 150d/300d/max
        for auto_adj in (True, False):
            for p in ("150d", "300d", "max"):
                try:
                    df = yf.download(symbol, period=p, interval="1d",
                                     auto_adjust=auto_adj, progress=False, threads=False)
                    c = _ensure_close(df)
                    if c is not None and len(c) >= min_len:
                        return df
                except Exception:
                    pass
        # 2) Ticker.history
        try:
            tk = yf.Ticker(symbol)
            for auto_adj in (True, False):
                df = tk.history(period="max", interval="1d", auto_adjust=auto_adj)
                c = _ensure_close(df)
                if c is not None and len(c) >= min_len:
                    return df
        except Exception:
            pass
        # 3) start/end（直近営業日-1まで）
        try:
            end = date.today() - timedelta(days=1)
            start = end - timedelta(days=600)
            for auto_adj in (True, False):
                df = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                                 interval="1d", auto_adjust=auto_adj, progress=False, threads=False)
                c = _ensure_close(df)
                if c is not None and len(c) >= min_len:
                    return df
        except Exception:
            pass
        return None

    def _pct_ret(series: pd.Series, lookback: int) -> Optional[float]:
        try:
            if series is None or not isinstance(series, pd.Series):
                return None
            if len(series) < lookback + 1:
                return None
            c0 = float(series.iloc[-(lookback + 1)])
            c1 = float(series.iloc[-1])
            if c0 == 0:
                return None
            return (c1 / c0 - 1.0) * 100.0
        except Exception:
            return None

    def _vol_ratio(volume: Optional[pd.Series], window: int = 20) -> Optional[float]:
        try:
            if volume is None or not isinstance(volume, pd.Series):
                return None
            v = pd.to_numeric(volume, errors="coerce").ffill().dropna()
            if len(v) < window + 1:
                return None
            ma = v.rolling(window).mean()
            v_last = float(v.iloc[-1])
            v_ma = float(ma.iloc[-1])
            if v_ma <= 0:
                return None
            return v_last / v_ma
        except Exception:
            return None

    def _latest_nonempty_history() -> Optional[str]:
        files = sorted(glob(os.path.join(mdir, "indexes_*.json")))
        files = [p for p in files if os.path.basename(p) != os.path.basename(out_path)]
        for p in reversed(files):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict) and isinstance(obj.get("data"), list) and len(obj["data"]) > 0:
                    return p
            except Exception:
                pass
        return None

    # オンラインアクセス試験（軽いテスト）
    online_ok = True
    try:
        test = yf.download("SPY", period="5d", interval="1d", auto_adjust=True,
                           progress=False, threads=False)
        online_ok = _ensure_close(test) is not None
    except Exception:
        online_ok = False

    collected = 0
    if online_ok:
        req_len = 21
        for name, syms in ALIASES.items():
            got = False
            for symbol in syms:
                df = _try_download(symbol, min_len=req_len)
                if df is None:
                    print(f"[SKIP] {name}:{symbol} → df None/short")
                    continue

                close = _ensure_close(df)
                if close is None or len(close) < 2:
                    print(f"[SKIP] {name}:{symbol} → close too short")
                    continue

                volume = df["Volume"] if "Volume" in df.columns else None
                r1 = _pct_ret(close, 1)
                r5 = _pct_ret(close, 5)
                r20 = _pct_ret(close, 20)

                if (r5 is None or r20 is None) and len(close) < 60:
                    df2 = _try_download(symbol, min_len=60)
                    if df2 is not None:
                        c2 = _ensure_close(df2)
                        if c2 is not None:
                            close = c2
                            r1 = _pct_ret(close, 1)
                            r5 = _pct_ret(close, 5)
                            r20 = _pct_ret(close, 20)
                        volume = df2["Volume"] if df2 is not None and "Volume" in df2.columns else volume

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
                collected += 1
                got = True
                break

            if not got:
                print(f"[MISS] {name} → 全候補NG（今回は見送り）")

        # 収集ゼロなら“強制最終トライ”
        if collected == 0:
            print("[RESCUE] 強制最終トライ（SPY/QQQ/GC=F/USDJPY=X）")
            rescue = {
                "SPX": ["SPY", "^GSPC"],
                "NDX": ["QQQ", "^NDX"],
                "GOLD": ["GC=F", "GLD"],
                "USDJPY": ["USDJPY=X"],
            }
            for name, syms in rescue.items():
                for symbol in syms:
                    df = _try_download(symbol, min_len=21)
                    if df is None:
                        continue
                    close = _ensure_close(df)
                    if close is None or len(close) < 2:
                        continue
                    volume = df["Volume"] if "Volume" in df.columns else None
                    r1 = _pct_ret(close, 1)
                    r5 = _pct_ret(close, 5)
                    r20 = _pct_ret(close, 20)
                    vr = _vol_ratio(volume, 20) if volume is not None else None
                    if r1 is None and r5 is None and r20 is None and vr is None:
                        continue
                    out["data"].append({
                        "symbol": name,
                        "ret_1d": None if r1 is None else round(r1, 2),
                        "ret_5d": None if r5 is None else round(r5, 2),
                        "ret_20d": None if r20 is None else round(r20, 2),
                        "vol_ratio": None if vr is None else round(vr, 2),
                    })
                    collected += 1
                    print(f"[OK-RESCUE] {name}:{symbol}")
                    break

    # ========= ここからオフライン/収集ゼロ時の“自動”フォールバック =========
    if len(out["data"]) == 0:
        # 1) 直近の非空ファイルをコピー
        prev = _latest_nonempty_history()
        if prev:
            try:
                with open(prev, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict) and isinstance(obj.get("data"), list) and len(obj["data"]) > 0:
                    out["data"] = obj["data"]
                    print(f"[OFFLINE] copied from {os.path.basename(prev)} → {os.path.basename(out_path)} ({len(out['data'])} symbols)")
            except Exception as e:
                print(f"[WARN] history read failed: {e}")

    if len(out["data"]) == 0:
        # 2) 完全初回：ニュートラル合成を自動生成（0%・vol_ratio=1.0）
        synth = []
        for name in SYNTHETIC_MIN:
            synth.append({
                "symbol": name,
                "ret_1d": 0.0,
                "ret_5d": 0.0,
                "ret_20d": 0.0,
                "vol_ratio": 1.0,
            })
        out["data"] = synth
        print(f"[SYNTH] generated neutral snapshot ({len(out['data'])} symbols)")

    # 保存
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote: {out_path} ({len(out['data'])} symbols)")
    return out