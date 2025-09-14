from django.shortcuts import render
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.http import require_GET

from .services.trend import detect_trend


def main(request):
    """
    トップページ：簡易カード（ダミー）
    """
    cards = [
        {"name": "トヨタ", "ticker": "7203.T", "trend": "UP", "proba": 62.5},
        {"name": "ソニーG", "ticker": "6758.T", "trend": "FLAT", "proba": None},
    ]
    return render(request, "main.html", {"cards": cards})


@require_GET
def trend_api(request):
    """
    JSON API: /api/trend?ticker=XXXX
    """
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
    """
    画面: /trend/
    スマホファーストのシンプル画面（HTMXでカード差し替え）
    """
    return render(request, "portfolio/trend.html")


def trend_card_partial(request):
    """
    HTMX が差し替えるカード断片: /trend/card?ticker=XXXX
    """
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


def healthz(request):
    """
    ヘルスチェック: /healthz
    """
    return HttpResponse("ok")
