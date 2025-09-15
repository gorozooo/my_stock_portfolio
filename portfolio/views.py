from django.shortcuts import render
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET

from .services.trend import detect_trend

import yfinance as yf
import numpy as np
import pandas as pd


# 必要ならトップページ（ダミー）を残す
def main(request):
    cards = [
        {"name": "トヨタ自動車", "ticker": "7203.T", "trend": "UP", "proba": 62.5},
        {"name": "ソニーグループ", "ticker": "6758.T", "trend": "FLAT", "proba": None},
    ]
    return render(request, "main.html", {"cards": cards})


@require_GET
def trend_api(request):
    ticker = (request.GET.get("ticker") or "").strip()
    if not ticker:
        return HttpResponseBadRequest("ticker is required")
    try:
        result = detect_trend(ticker)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    data = {
        "ok": True,
        "ticker": result.ticker,
        "name": result.name,  # 日本語名（なければ英語/コード）
        "asof": result.asof,
        "days": result.days,
        "signal": result.signal,
        "reason": result.reason,
        "slope": result.slope,
        "slope_annualized_pct": result.slope_annualized_pct,
        "ma_short": result.ma_short,
        "ma_long": result.ma_long,
    }
    return JsonResponse(data)


def trend_page(request):
    # スマホファーストのシンプル画面
    return render(request, "portfolio/trend.html")


def trend_card_partial(request):
    """
    HTMX が差し替えるカード断片
    """
    ticker = (request.GET.get("ticker") or "").strip()
    ctx = {"error": None, "res": None}

    if not ticker:
        ctx["error"] = "ティッカーを入力してください（例：AAPL, MSFT, 7203 など。日本株は .T 不要）"
        return render(request, "portfolio/_trend_card.html", ctx)

    try:
        ctx["res"] = detect_trend(ticker)
    except Exception as e:
        ctx["error"] = str(e)

    return render(request, "portfolio/_trend_card.html", ctx)
    
    @require_GET
def ohlc_api(request):
    t = (request.GET.get("ticker") or "").strip()
    days = int(request.GET.get("days", 180))
    if not t:
        return JsonResponse({"ok": False, "error": "ticker required"}, status=400)
    # 休日分バッファ
    period = max(days + 30, 240)
    df = yf.download(t, period=f"{period}d", interval="1d", progress=False)
    if df is None or df.empty:
        return JsonResponse({"ok": False, "error": "no data"}, status=404)

    s = df["Close"].dropna().tail(days)
    ma10 = s.rolling(10).mean()
    ma30 = s.rolling(30).mean()

    payload = {
        "ok": True,
        "ticker": t,
        "labels": [d.strftime("%Y-%m-%d") for d in s.index],
        "close": [float(x) for x in s.values],
        "ma10":  [None if pd.isna(x) else float(x) for x in ma10.values],
        "ma30":  [None if pd.isna(x) else float(x) for x in ma30.values],
    }
    return JsonResponse(payload)
    