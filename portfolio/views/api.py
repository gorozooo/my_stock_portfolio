from django.views.decorators.http import require_GET
from django.http import JsonResponse, HttpResponseBadRequest
import pandas as pd
import yfinance as yf
import numpy as np

from ..services.metrics import get_metrics
from ..services.trend import _normalize_ticker
from ..models import UserSetting  # ユーザー設定モデル（あれば）

@require_GET
def ohlc(request):
    """OHLC + MA API"""
    def tolist1d(series: pd.Series) -> list:
        if series is None:
            return []
        arr = np.ravel(series.to_numpy())
        return [float(v) if not pd.isna(v) else None for v in arr]

    raw = (request.GET.get("ticker") or "").strip()
    if not raw:
        return JsonResponse({"ok": False, "error": "ticker required"})
    ticker = raw if "." in raw else (raw.upper() + ".T" if 4 <= len(raw) <= 5 else raw)
    days = int(request.GET.get("days") or 180)

    try:
        df = yf.download(str(ticker), period=f"{days}d", interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or df.empty:
            return JsonResponse({"ok": False, "error": "no data"})

        def pick(df_: pd.DataFrame, name: str) -> pd.Series:
            if isinstance(df_.columns, pd.MultiIndex):
                if name in df_.columns.get_level_values(0):
                    obj = df_.xs(name, axis=1, level=0, drop_level=True)
                    s = obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj
                    return pd.to_numeric(s, errors="coerce")
                return pd.to_numeric(df_.iloc[:, -1], errors="coerce")
            for c in df_.columns:
                if str(c).lower() == name.lower():
                    return pd.to_numeric(df_[c], errors="coerce")
            raise KeyError(name)

        o = pick(df, "Open")
        h = pick(df, "High")
        l = pick(df, "Low")
        c = pick(df, "Close")

        base = pd.concat([o, h, l, c], axis=1, join="inner").dropna()
        base.columns = ["Open", "High", "Low", "Close"]
        if base.empty:
            return JsonResponse({"ok": False, "error": "no aligned data"})

        ma10_s = base["Close"].rolling(10).mean()
        ma30_s = base["Close"].rolling(30).mean()

        labels = base.index.strftime("%Y-%m-%d").tolist()
        close = tolist1d(base["Close"])
        ma10 = tolist1d(ma10_s)
        ma30 = tolist1d(ma30_s)

        ohlc = [
            {
                "x": idx.strftime("%Y-%m-%d"),
                "o": float(row["Open"]),
                "h": float(row["High"]),
                "l": float(row["Low"]),
                "c": float(row["Close"]),
            }
            for idx, row in base.iterrows()
        ]

        return JsonResponse({
            "ok": True,
            "labels": labels,
            "close": close,
            "ma10": ma10,
            "ma30": ma30,
            "ohlc": ohlc,
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


@require_GET
def metrics(request):
    ticker_raw = (request.GET.get("ticker") or "").strip()
    bench = (request.GET.get("bench") or "^TOPX").strip()
    if not ticker_raw:
        return HttpResponseBadRequest("ticker is required")
    ticker = _normalize_ticker(ticker_raw)

    equity = None
    risk = 1.0
    lot = None

    # ユーザー設定を反映
    try:
        if request.user and request.user.is_authenticated:
            setting, _ = UserSetting.objects.get_or_create(user=request.user)
            equity = setting.account_equity or equity
            risk = float(setting.risk_pct or risk)
    except Exception:
        pass

    # 任意：クエリで上書き（検証用）
    if request.GET.get("equity"):
        try: equity = int(request.GET.get("equity"))
        except: pass
    if request.GET.get("risk"):
        try: risk = float(request.GET.get("risk"))
        except: pass
    if request.GET.get("lot"):
        try: lot = int(request.GET.get("lot"))
        except: pass

    try:
        res = get_metrics(
            ticker,
            bench=bench,
            account_equity=equity,
            risk_pct=risk,
            lot=lot,
        )
        res["ticker"] = ticker
        res["bench"] = bench
        return JsonResponse(res)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)