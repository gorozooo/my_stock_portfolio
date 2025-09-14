from django.shortcuts import render

def main(request):
    # まずは最小のダミーデータ（後でAIカードに差し替え）
    cards = [
        {"name": "トヨタ", "ticker": "7203.T", "trend": "UP", "proba": 62.5},
        {"name": "ソニーG", "ticker": "6758.T", "trend": "FLAT", "proba": None},
    ]
    return render(request, "main.html", {"cards": cards})

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET
from django.shortcuts import render
from .services.trend import detect_trend

@require_GET
def trend_api(request):
    ticker = request.GET.get("ticker", "").strip()
    if not ticker:
        return HttpResponseBadRequest("ticker is required")
    try:
        result = detect_trend(ticker)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    data = {
        "ok": True,
        "ticker": result.ticker,
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
    """HTMX が差し替えるカード断片"""
    ticker = request.GET.get("ticker", "").strip()
    ctx = {"error": None, "res": None}
    if not ticker:
        ctx["error"] = "ティッカーを入力してください（例：AAPL, MSFT, 7203.T）"
        return render(request, "portfolio/_trend_card.html", ctx)
    try:
        ctx["res"] = detect_trend(ticker)
    except Exception as e:
        ctx["error"] = str(e)
    return render(request, "portfolio/_trend_card.html", ctx)
