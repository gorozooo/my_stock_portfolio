# portfolio/services/metrics.py
from __future__ import annotations
import re
import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252

# --- 4〜5桁の日本株コードは .T を付ける（バックエンドと同じルール） ---
_JP_ALNUM = re.compile(r"^[0-9A-Z]{4,5}$")

def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    return f"{t}.T" if _JP_ALNUM.match(t) else t

def _get_close(df: pd.DataFrame) -> pd.Series:
    """
    yfinanceの列ゆらぎを吸収してClose系列を返す（auto_adjust=True想定）
    必ず 1 次元 Series を返す（MultiIndex の場合も squeeze）。
    """
    s = None
    if "Close" in df.columns:
        s = df["Close"]
    elif "Adj Close" in df.columns:
        s = df["Adj Close"]
    else:
        # マルチインデックス対策
        for c in df.columns:
            if isinstance(c, tuple) and c and c[0] in ("Close", "Adj Close"):
                s = df[c]
                break
        if s is None:
            raise KeyError("Close")

    # DataFrame になってしまうケースを 1 列に絞って Series にする
    if isinstance(s, pd.DataFrame):
        if s.shape[1] == 0:
            raise KeyError("Close")
        s = s.iloc[:, 0]

    return pd.to_numeric(s, errors="coerce").dropna()

def _ann_vol(ret: pd.Series) -> float:
    return float(ret.std() * np.sqrt(TRADING_DAYS) * 100.0)

def _adx(df: pd.DataFrame, n: int = 14) -> float:
    """Wilder法に準じた簡易ADX（dfはHigh/Low/Close必須）"""
    h, l, c = df["High"], df["Low"], df["Close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index)

    tr = pd.concat([
        (h - l).abs(),
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)

    # Wilder 平滑（EMA で近似）
    alpha = 1 / n
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return float(adx.dropna().iloc[-1])

def get_metrics(ticker: str, bench: str = "^TOPX", days: int = 420) -> dict:
    """
    プロ向け軽量根拠セットを返す。
    - ティッカーはここで必ず正規化（例: '7011' -> '7011.T'）
    - 返却は views 側のフロント想定スキーマ
    """
    ticker = _normalize_ticker(ticker)

    # 価格
    df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("no data")

    s = _get_close(df)
    ret = s.pct_change()

    # ベンチ
    try:
        bdf = yf.download(bench, period=f"{days}d", interval="1d", progress=False)
        if bdf is not None and not bdf.empty:
            b = _get_close(bdf)
            bret = b.pct_change()
        else:
            b = None
            bret = None
    except Exception:
        b = None
        bret = None

    # トレンド：回帰傾き（60日・年率）
    y = s.tail(60).to_numpy(dtype=float)           # ← 常に 1 次元に
    x = np.arange(len(y), dtype=float)
    if len(y) < 2:
        raise ValueError("insufficient data for regression")
    k, _ = np.polyfit(x, y, 1)
    slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    ma20  = float(s.rolling(20).mean().iloc[-1])   if len(s) >= 20  else None
    ma50  = float(s.rolling(50).mean().iloc[-1])   if len(s) >= 50  else None
    ma200 = float(s.rolling(200).mean().iloc[-1])  if len(s) >= 200 else None
    ma_stack = (
        "bull" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 > ma50 > ma200)
        else "bear" if (ma20 is not None and ma50 is not None and ma200 is not None and ma20 < ma50 < ma200)
        else "mixed"
    )

    # ADX(14)
    need = ["High", "Low", "Close"]
    if not set(need).issubset(df.columns if isinstance(df.columns, pd.Index) else []):
        # MultiIndex対策：上で _get_close は処理済みだが、ADXは素直な列名を期待するため整形
        cols = {}
        for want in need:
            found = None
            if isinstance(df.columns, pd.MultiIndex):
                for c in df.columns:
                    if isinstance(c, tuple) and c[0] == want:
                        found = df[c]
                        break
            else:
                if want in df.columns:
                    found = df[want]
            if found is None:
                raise ValueError(f"missing column for ADX: {want}")
            cols[want] = pd.to_numeric(found, errors="coerce")
        adx14 = _adx(pd.DataFrame(cols).dropna()[need])
    else:
        adx14 = _adx(df.dropna()[need])

    # 相対強さ：6か月超過（126営業日）
    rs_6m = None
    if bret is not None and len(s) > 126:
        try:
            rs_6m = float((s.pct_change(126).iloc[-1] - b.pct_change(126).iloc[-1]) * 100)
        except Exception:
            rs_6m = None

    # 52週高値/安値乖離
    roll_max = s.rolling(252).max().iloc[-1] if len(s) >= 252 else None
    roll_min = s.rolling(252).min().iloc[-1] if len(s) >= 252 else None
    from_52w_high = float((s.iloc[-1] / roll_max - 1) * 100) if roll_max else None
    from_52w_low  = float((s.iloc[-1] / roll_min - 1) * 100) if roll_min else None

    # リスク・流動性
    vol20 = _ann_vol(ret.tail(20).dropna()) if len(ret.dropna()) >= 20 else None
    vol60 = _ann_vol(ret.tail(60).dropna()) if len(ret.dropna()) >= 60 else None

    tr = pd.concat([
        (df["High"] - df["Low"]).abs(),
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"]  - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])

    adv20 = float((s * df["Volume"]).rolling(20).mean().iloc[-1]) if "Volume" in df.columns else None

    return {
        "ok": True,
        "asof": str(s.index[-1].date()),
        "trend": {
            "slope_ann_pct_60": slope_ann_pct,
            "ma": {"20": ma20, "50": ma50, "200": ma200, "stack": ma_stack},
            "adx14": adx14
        },
        "relative": {
            "rs_6m_pct": rs_6m,
            "from_52w_high_pct": from_52w_high,
            "from_52w_low_pct":  from_52w_low
        },
        "risk": {
            "vol20_ann_pct": vol20,
            "vol60_ann_pct": vol60,
            "atr14": atr14
        },
        "liquidity": {
            "adv20": adv20
        }
    }