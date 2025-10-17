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
    主要指数の1d/5d/20dリターンと出来高比を作成して media/market/indexes_YYYY-MM-DD.json へ保存。
    - オンライン(yfinance)で取れない場合は手動ファイル/過去履歴でフォールバック
    - 手動ファイル: media/market/indexes_manual.json
      形式: {"date":"YYYY-MM-DD","data":[{"symbol":"SPX","ret_5d":1.2,"ret_20d":4.5,"vol_ratio":1.05}, ...]}
      ※ ret_1d は省略可
    """
    import yfinance as yf
    import pandas as pd
    from datetime import date
    import os, json

    today = date.today().isoformat()
    mdir = _market_dir()
    os.makedirs(mdir, exist_ok=True)
    out_path = os.path.join(mdir, f"indexes_{today}.json")
    out: Dict[str, Any] = {"date": today, "data": []}

    # --- 小ヘルパ ---
    def _pct_return(close: "pd.Series", lookback: int) -> Optional[float]:
        try:
            if close is None or len(close) < lookback + 1:
                return None
            c0 = float(close.iloc[-(lookback + 1)])
            c1 = float(close.iloc[-1])
            if c0 == 0:
                return None
            return (c1 / c0 - 1.0) * 100.0
        except Exception:
            return None

    def _vol_ratio(volume: Optional["pd.Series"], lookback: int) -> Optional[float]:
        try:
            if volume is None or len(volume.dropna()) < lookback + 1:
                return None
            v0 = float(volume.iloc[-(lookback + 1)])
            v1 = float(volume.iloc[-1])
            if v0 <= 0:
                return None
            return v1 / v0
        except Exception:
            return None

    def _log(msg: str):
        print(msg)

    # 代替シンボル候補
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

    # まずオンラインで取れるか簡易判定（SPYを3日）
    online_ok = True
    try:
        test = yf.download("SPY", period="3d", interval="1d", progress=False, threads=False)
        online_ok = bool(test is not None and len(test) >= 2 and "Close" in test.columns)
    except Exception:
        online_ok = False

    # ========== オンラインで取れる場合 ==========
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
    _log("[OFFLINE] yfinance 取得不可 → 手動/履歴にフォールバック")

    # 1) 手動ファイル
    manual_path = os.path.join(mdir, "indexes_manual.json")
    if os.path.exists(manual_path):
        try:
            with open(manual_path, "r", encoding="utf-8") as f:
                manual = json.load(f)
            if isinstance(manual, dict) and isinstance(manual.get("data"), list):
                # そのまま今日の日付で保存
                out["data"] = manual["data"]
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
                print(f"[OFFLINE] used manual → {out_path} ({len(out['data'])} symbols)")
                return out
        except Exception as e:
            _log(f"[WARN] manual read failed: {e}")

    # 2) 直近の履歴から転写（せめて空でないファイルを今日の日付で再保存）
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

    # 3) 何も無ければ“ダミー0”で保存（以降の処理は問題なく流れる）
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[OFFLINE] no data available → wrote empty: {out_path} (0 symbols)")
    return out