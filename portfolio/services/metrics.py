# portfolio/services/metrics.py
from __future__ import annotations
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252

# =========================================================
# ヘルパー：Series 取り出し（yfinance の単独 / MultiIndex どちらも安全）
# =========================================================
def _pick_series(df: pd.DataFrame, field: str, *, required: bool = True) -> pd.Series:
    """
    指定 field ('Open','High','Low','Close','Adj Close','Volume') を安全に Series で返す。
    - MultiIndex のとき level=0 を見て xs 抜き取り、最初の列を採用
    - Close がない場合は Adj Close を許容
    - required=False のとき無ければ空 Series
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        if required:
            raise ValueError("empty dataframe")
        return pd.Series(dtype=float)

    # MultiIndex
    if isinstance(df.columns, pd.MultiIndex):
        lv0 = df.columns.get_level_values(0)
        tgt = field
        alt = "Adj Close" if field.lower() == "close" else None
        if tgt in lv0:
            obj = df.xs(tgt, axis=1, level=0, drop_level=True)
        elif alt and (alt in lv0):
            obj = df.xs(alt, axis=1, level=0, drop_level=True)
        else:
            if required:
                raise ValueError(f"{field} column not found")
            return pd.Series(dtype=float)
        s = obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj
        return pd.to_numeric(s, errors="coerce")

    # 単一列 Index
    cols_lower = {str(c).lower(): c for c in df.columns}
    use = cols_lower.get(field.lower())
    if use is None and field.lower() == "close":
        use = cols_lower.get("adj close")
    if use is None:
        if required:
            raise ValueError(f"{field} column not found")
        return pd.Series(dtype=float)
    return pd.to_numeric(df[use], errors="coerce")


# =========================================================
# 指標計算ヘルパー
# =========================================================
def _ann_vol(ret: pd.Series) -> Optional[float]:
    try:
        return float(ret.std() * np.sqrt(TRADING_DAYS) * 100.0)
    except Exception:
        return None

def _adx(df: pd.DataFrame, n: int = 14) -> Optional[float]:
    try:
        h = pd.to_numeric(df["High"], errors="coerce")
        l = pd.to_numeric(df["Low"],  errors="coerce")
        c = pd.to_numeric(df["Close"], errors="coerce")

        up = h.diff()
        dn = -l.diff()
        plus_dm  = pd.Series(np.where((up > dn) & (up > 0), up, 0.0),  index=h.index, dtype=float)
        minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h.index, dtype=float)

        tr = pd.concat([
            (h - l).abs(),
            (h - c.shift()).abs(),
            (l - c.shift()).abs()
        ], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1/max(1, n), adjust=False).mean()
        plus_di  = 100 * plus_dm.ewm(alpha=1/max(1, n), adjust=False).mean()  / atr
        minus_di = 100 * minus_dm.ewm(alpha=1/max(1, n), adjust=False).mean() / atr
        dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.ewm(alpha=1/max(1, n), adjust=False).mean()
        val = adx.dropna()
        return float(val.iloc[-1]) if not val.empty else None
    except Exception:
        return None


# =========================================================
# 取引単元（日本株:100株 / それ以外:1株 の簡易ルール）
# =========================================================
def _default_lot_for(ticker: str) -> int:
    t = (ticker or "").upper()
    # 例外があれば将来ここにテーブルを足す
    return 100 if t.endswith(".T") or t.isdigit() else 1


# =========================================================
# メイン：メトリクス生成
# =========================================================
def get_metrics(
    ticker: str,
    bench: str = "^TOPX",
    days: int = 420,
    *,
    account_equity: float | None = None,   # 例: 1_000_000 (=100万円)
    risk_pct: float = 1.0,                 # 例: 1% リスク
    lot: int | None = None                 # Noneなら自動判定（日本株=100）
) -> Dict[str, Any]:
    # --- 価格データ取得（auto_adjust 明示）
    df = yf.download(
        ticker,
        period=f"{days}d",
        interval="1d",
        auto_adjust=True,         # 将来のデフォルト変更に備え明示
        progress=False,
    )
    if df is None or df.empty:
        raise ValueError("no data")

    # 必要列を抽出
    close_s = _pick_series(df, "Close", required=True).dropna()
    high_s  = _pick_series(df, "High",  required=True).dropna()
    low_s   = _pick_series(df, "Low",   required=True).dropna()
    vol_s   = _pick_series(df, "Volume", required=False)

    if close_s.empty or high_s.empty or low_s.empty:
        raise ValueError("no aligned data")

    # 共通インデックスに揃える
    common = close_s.index.intersection(high_s.index).intersection(low_s.index)
    s = close_s.reindex(common)
    h = high_s.reindex(common)
    l = low_s.reindex(common)
    v = vol_s.reindex(common) if not vol_s.empty else pd.Series(dtype=float, index=common)

    ret = s.pct_change()

    # --- ベンチ
    b = None; bret = None
    try:
        bdf = yf.download(bench, period=f"{days}d", interval="1d",
                          auto_adjust=True, progress=False)
        if bdf is not None and not bdf.empty:
            bclose = _pick_series(bdf, "Close", required=True).dropna()
            joined = pd.concat([s.pct_change(), bclose.pct_change()], axis=1, join="inner")
            joined.columns = ["s", "b"]
            b = bclose
            bret = joined["b"]
    except Exception:
        pass

    # --- トレンド（60日回帰傾き：年率換算%）
    y = np.ravel(s.tail(60).to_numpy(dtype=float))
    if y.size < 2:
        raise ValueError("not enough data for regression")
    x = np.arange(y.size, dtype=float)
    k, _ = np.polyfit(x, y, 1)
    slope_ann_pct = float((k / y[-1]) * 100.0 * TRADING_DAYS)

    # --- 移動平均
    ma20  = float(s.rolling(20).mean().iloc[-1])   if len(s) >= 20  else None
    ma50  = float(s.rolling(50).mean().iloc[-1])   if len(s) >= 50  else None
    ma200 = float(s.rolling(200).mean().iloc[-1])  if len(s) >= 200 else None
    if all(x is not None for x in (ma20, ma50, ma200)):
        ma_stack = "bull" if (ma20 > ma50 > ma200) else ("bear" if (ma20 < ma50 < ma200) else "mixed")
    else:
        ma_stack = "mixed"

    # --- ADX
    dfx = pd.DataFrame({"High": h, "Low": l, "Close": s}).dropna(how="any")
    adx14 = _adx(dfx[["High", "Low", "Close"]])

    # --- RS(6M)
    rs_6m = None
    try:
        if b is not None and len(s) > 126 and len(b) > 126:
            s6 = s.pct_change(126)
            b6 = b.pct_change(126)
            joined6 = pd.concat([s6, b6], axis=1, join="inner").dropna()
            if not joined6.empty:
                rs_6m = float((joined6.iloc[-1, 0] - joined6.iloc[-1, 1]) * 100.0)
    except Exception:
        rs_6m = None

    # --- 52週高安＆終値
    last = float(s.iloc[-1])
    from_52w_high = from_52w_low = None
    if len(s) >= 252:
        roll_max = float(s.tail(252).max())
        roll_min = float(s.tail(252).min())
        from_52w_high = float((last / roll_max - 1.0) * 100.0) if roll_max else None
        from_52w_low  = float((last / roll_min - 1.0) * 100.0) if roll_min else None

    # --- 年化ボラ
    ret_clean = ret.dropna()
    vol20 = _ann_vol(ret_clean.tail(20)) if len(ret_clean) >= 20 else None
    vol60 = _ann_vol(ret_clean.tail(60)) if len(ret_clean) >= 60 else None

    # --- ATR14
    tr = pd.concat([
        (h - l).abs(),
        (h - s.shift()).abs(),
        (l - s.shift()).abs()
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1]) if tr.dropna().size >= 14 else None

    # --- スイング水準（エントリー/ストップ指針）
    swing_win  = 20
    swing_high = float(h.tail(swing_win).max())
    swing_low  = float(l.tail(swing_win).min())

    entry_level = stop_level = None
    if atr14 is not None:
        entry_level = float(swing_high + 0.5 * atr14)  # 指針：上抜け買い
        stop_level  = float(swing_low  - 1.5 * atr14)  # 指針：初期ストップ
    last_close = last

    # --- ADV20（売買代金20日平均）
    adv20 = None
    try:
        if not v.empty:
            adv20 = int(round(float((s * v).rolling(20).mean().iloc[-1])))
    except Exception:
        adv20 = None

    # --- ポジションサイズ計算（ロング前提）
    sizing: Optional[Dict[str, Any]] = None
    try:
        if (
            entry_level is not None and
            stop_level is not None and
            entry_level > stop_level and
            account_equity is not None and
            float(account_equity) > 0.0 and
            float(risk_pct) > 0.0
        ):
            risk_amount = float(account_equity) * float(risk_pct) / 100.0
            risk_per_share = float(entry_level - stop_level)
            if risk_per_share > 0:
                _lot = int(lot) if (isinstance(lot, int) and lot > 0) else _default_lot_for(ticker)
                raw_qty = risk_amount / risk_per_share
                units = int(raw_qty // _lot) * _lot  # 単元丸め
                if units > 0:
                    notional = float(units) * float(entry_level)
                    exp_loss = float(units) * float(risk_per_share)
                    # 参考ターゲット（1.5R）
                    target_price = float(entry_level + 1.5 * risk_per_share)
                    r_multiple_to_hi = None
                    try:
                        denom = (entry_level - stop_level)
                        r_multiple_to_hi = float((swing_high - entry_level) / denom) if denom > 0 else None
                    except Exception:
                        r_multiple_to_hi = None

                    sizing = {
                        "equity": float(account_equity),
                        "risk_pct": float(risk_pct),
                        "risk_amount": float(risk_amount),
                        "lot": int(_lot),
                        "qty": int(units),
                        "risk_per_share": float(risk_per_share),
                        "notional": int(round(notional)),
                        "expected_loss_at_stop": int(round(exp_loss)),
                        "suggested_target_price": float(target_price),
                        "r_multiple_to_prev_high": float(r_multiple_to_hi) if r_multiple_to_hi is not None else None,
                    }
                else:
                    # 最小単元がリスク額に対して大きすぎるケース
                    sizing = {
                        "equity": float(account_equity),
                        "risk_pct": float(risk_pct),
                        "risk_amount": float(risk_amount),
                        "lot": int(_lot),
                        "qty": 0,
                        "risk_per_share": float(risk_per_share),
                        "notional": 0,
                        "expected_loss_at_stop": 0,
                        "reason": "リスク額に対して最小単元が大きすぎます",
                    }
    except Exception:
        sizing = None

    return {
        "ok": True,
        "asof": str(s.index[-1].date()),
        "trend": {
            "slope_ann_pct_60": slope_ann_pct,
            "ma": {"20": ma20, "50": ma50, "200": ma200, "stack": ma_stack},
            "adx14": adx14,
        },
        "relative": {
            "rs_6m_pct": rs_6m,
            "from_52w_high_pct": from_52w_high,
            "from_52w_low_pct":  from_52w_low,
        },
        "risk": {
            "vol20_ann_pct": vol20,
            "vol60_ann_pct": vol60,
            "atr14": atr14,
        },
        "liquidity": {
            "adv20": adv20,
        },
        "levels": {
            "entry": entry_level,
            "stop":  stop_level,
            "swing_high": swing_high,
            "swing_low":  swing_low,
            "last_close": last_close,
            "atr14": atr14,
            "window": swing_win,
        },
        # ← 追加（None の可能性あり）：フロントは存在チェックして描画
        "sizing": sizing,
    }
    
# =============================
# 単銘柄の最新終値を取得
# =============================
import yfinance as yf

def get_latest_price(ticker: str) -> float | None:
    """
    最新の終値（前日まで）を返す。取得不可なら None。
    例: '7011.T'
    """
    try:
        if not ticker:
            return None
        df = yf.download(ticker, period="3d", interval="1d", progress=False)
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception as e:
        print(f"[get_latest_price ERROR] {ticker}: {e}")
        return None
