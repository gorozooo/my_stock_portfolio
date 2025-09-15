from __future__ import annotations

from django.shortcuts import render
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET

from .services.trend import detect_trend

import re
import pandas as pd
import yfinance as yf


# ========= 共通: ティッカー正規化（日本株 4〜5桁は .T を付与） =========
_JP_ALNUM = re.compile(r"^[0-9A-Z]{4,5}$")

def _normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return t
    if "." in t:
        return t
    if _JP_ALNUM.match(t):
        return f"{t}.T"
    return t


# ========= 画面 =========
def main(request):
    # 必要ならトップページ（ダミー）
    cards = [
        {"name": "トヨタ自動車", "ticker": "7203.T", "trend": "UP", "proba": 62.5},
        {"name": "ソニーグループ", "ticker": "6758.T", "trend": "FLAT", "proba": None},
    ]
    return render(request, "main.html", {"cards": cards})


def trend_page(request):
    # スマホファーストのシンプル画面
    return render(request, "portfolio/trend.html")


def trend_card_partial(request):
    """
    HTMX が差し替えるカード断片
    """
    ticker_raw = (request.GET.get("ticker") or "").strip()
    ticker = _normalize_ticker(ticker_raw)
    ctx = {"error": None, "res": None}

    if not ticker:
        ctx["error"] = "ティッカーを入力してください（例：AAPL, MSFT, 7203 など。日本株は .T 不要）"
        return render(request, "portfolio/_trend_card.html", ctx)

    try:
        ctx["res"] = detect_trend(ticker)
    except Exception as e:
        ctx["error"] = str(e)

    return render(request, "portfolio/_trend_card.html", ctx)


# ========= API =========
@require_GET
def trend_api(request):
    ticker_raw = (request.GET.get("ticker") or "").strip()
    if not ticker_raw:
        return HttpResponseBadRequest("ticker is required")

    ticker = _normalize_ticker(ticker_raw)

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


@require_GET
def ohlc_api(request):
    # ★ ここでも必ず正規化（7011 → 7011.T）
    ticker_raw = (request.GET.get("ticker") or "").strip()
    ticker = _normalize_ticker(ticker_raw)
    days = int(request.GET.get("days") or 180)

    if not ticker:
        return JsonResponse({"ok": False, "error": "ticker required"})

    try:
        df = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
        if df is None or df.empty:
            return JsonResponse({"ok": False, "error": "no data"})

        s = df["Close"].dropna()
        ma10 = s.rolling(10).mean()
        ma30 = s.rolling(30).mean()

        data = {
            "ok": True,
            "labels": [d.strftime("%Y-%m-%d") for d in s.index],
            "close": [float(v) for v in s],
            "ma10": [float(v) if pd.notna(v) else None for v in ma10],
            "ma30": [float(v) if pd.notna(v) else None for v in ma30],
        }
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})