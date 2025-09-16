# portfolio/views/api.py
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from ..models import UserSetting
from ..services.metrics import get_metrics
import yfinance as yf
import pandas as pd

def _pick(s, n):
    return [float(x) if x is not None else None for x in s.tail(n)]

@login_required
def metrics(request):
    """ユーザー設定(account_equity, risk_pct)を自動反映して返す"""
    ticker = request.GET.get("ticker", "").strip()
    if not ticker:
        return JsonResponse({"ok": False, "error": "ticker required"}, status=400)

    setting, _ = UserSetting.objects.get_or_create(user=request.user)
    try:
        res = get_metrics(
            ticker,
            account_equity=setting.account_equity,
            risk_pct=setting.risk_pct,
        )
        return JsonResponse(res)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)

@login_required
def ohlc(request):
    """チャート用の軽量データ: labels, close, ma10, ma30"""
    ticker = request.GET.get("ticker", "").strip()
    days = int(request.GET.get("days", "180"))
    if not ticker:
        return JsonResponse({"ok": False, "error": "ticker required"}, status=400)

    try:
        df = yf.download(ticker, period=f"{days}d", interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or df.empty:
            return JsonResponse({"ok": False, "error": "no data"}, status=404)
        s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        labels = [d.strftime("%Y-%m-%d") for d in s.index]
        ma10 = s.rolling(10).mean()
        ma30 = s.rolling(30).mean()
        return JsonResponse({
            "ok": True,
            "labels": labels,
            "close": [float(x) for x in s],
            "ma10": _pick(ma10, len(s)),
            "ma30": _pick(ma30, len(s)),
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)