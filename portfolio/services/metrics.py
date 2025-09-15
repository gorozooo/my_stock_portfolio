# portfolio/services/metrics.py
from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252

def _get_close(df: pd.DataFrame) -> pd.Series:
    """yfinanceの列ゆらぎを吸収してClose系列を返す（auto_adjust=True想定）"""
    if "Close" in df.columns:
        s = df["Close"]
    elif "Adj Close" in df.columns:
        s = df["Adj Close"]
    else:
        # マルチインデックス対策
        for c in df.columns:
            if isinstance(c, tuple) and c[0] in ("Close", "Adj Close"):
                s = df[c]
                break
        else:
            raise KeyError("Close")
    return s.dropna()

def _get_series(df: pd.DataFrame, field: str) -> pd.Series:
    """High/Low/Volume 等も安全に取得"""
    # 単一列
    if field in df.columns:
        return pd.to_numeric(df[field], errors="coerce").dropna()
    # マルチインデックス（level0が該当）
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        if field in lvl0:
            obj = df.xs(field, axis=1, level=0, drop_level=True)
            s = obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj
            return pd.to_numeric(s, errors="coerce").dropna()
    # 見つからなければ空
    return pd.Series(dtype=float)

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

    # Wilder近似：EWMAで代用（実装簡易化）
    alpha = 1 / n
    atr = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return float(adx.dropna().iloc[-1])

def get_metrics(ticker: str, bench: str = "^TOPX", days: int = 420) -> dict:
    # 価格
    df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
    if df is None or df.empty:
        raise ValueError("no data")

    s = _get_close(df)
    ret = s.pct_change()

    # High/Low/Volume も確保（無くても続行できるように）
    high = _get_series(df, "High")
    low  = _get_series(df, "Low")
    vol  = _get_series(df, "Volume")

    # ベンチ
    try:
        bdf = yf.download(bench, period=f"{days}d", interval="1d", progress=False)
        b = _get_close(bdf)
        bret = b.pct_change()
    except Exception:
        b = None
        bret = None

    # トレンド：回帰傾き（60日・年率）
    y = s.tail(60).values.astype(float)
    x = np.arange(len(y), dtype=float)
    k, _ = np.polyfit(x, y, 1)
    slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    ma20 = float(s.rolling(20).mean().iloc[-1])
    ma50 = float(s.rolling(50).mean().iloc[-1])
    ma200 = float(s.rolling(200).mean().iloc[-1])
    ma_stack = "bull" if ma20 > ma50 > ma200 else ("bear" if ma20 < ma50 < ma200 else "mixed")

    # ADX(14)（High/Low/Close必須）
    try:
        base = pd.concat([high, low, s], axis=1, join="inner")
        base.columns = ["High", "Low", "Close"]
        adx14 = _adx(base.dropna()[["High","Low","Close"]])
    except Exception:
        adx14 = None

    # 相対強さ：6か月超過（126営業日）
    rs_6m = None
    if bret is not None and len(s) > 126 and len(b) > 126:
        rs_6m = float((s.pct_change(126).iloc[-1] - b.pct_change(126).iloc[-1]) * 100)

    # 52週高値/安値乖離
    roll_max = s.rolling(252).max().iloc[-1] if len(s) >= 252 else None
    roll_min = s.rolling(252).min().iloc[-1] if len(s) >= 252 else None
    last_px  = float(s.iloc[-1])
    from_52w_high = float((last_px / roll_max - 1) * 100) if roll_max else None
    from_52w_low  = float((last_px / roll_min - 1) * 100) if roll_min else None

    # リスク・流動性
    vol20 = _ann_vol(ret.tail(20).dropna()) if len(ret.dropna()) >= 20 else None
    vol60 = _ann_vol(ret.tail(60).dropna()) if len(ret.dropna()) >= 60 else None

    tr = pd.concat([
        (high - low).abs(),
        (high - s.shift()).abs(),
        (low  - s.shift()).abs()
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1]) if len(tr.dropna()) >= 14 else None

    adv20 = None
    try:
        if not vol.empty:
            adv20 = float((s * vol).rolling(20).mean().iloc[-1])
    except Exception:
        adv20 = None

    # ========= ここから ② エントリー / 損切り / 利確 推奨値 =========
    # 方針:
    # - entry: 現在値（last_px）を基準表示
    # - stop : 「直近20日安値」 と 「entry - 2*ATR(14)」 の高い方を採用（極端にタイトにならないように）
    # - take : リスクリワード = 2.0 を基本（ take = entry + 2*(entry - stop) ）
    # - 参考: MA20 も併記（押し目/支持の目安）
    swing_low_20 = float(low.tail(20).min()) if not low.empty and len(low) >= 20 else None
    atr_val = atr14 or 0.0
    # デフォのストップ候補（ATRベース）
    stop_by_atr = last_px - 2.0 * atr_val if atr_val else None
    # 採用するストップ
    candidates = [v for v in [swing_low_20, stop_by_atr] if v and v > 0]
    stop_loss = max(candidates) if candidates else None

    entry = last_px
    take_profit = None
    rr = 2.0
    if stop_loss and stop_loss < entry:
        risk_per_share = entry - stop_loss
        take_profit = entry + rr * risk_per_share

    trade = {
        "entry": entry,
        "stop": stop_loss,
        "take": take_profit,
        "rr": rr,
        "ma20_hint": ma20,
        "method": "entry=現値 / stop=max(直近20日安値, entry-2*ATR14) / take=entry+2R"
    }

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
        },
        # ② エントリー/損切り/利確
        "trade": trade
    }