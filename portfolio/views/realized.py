# portfolio/views/realized.py
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, F
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import RealizedTrade
import csv
from django.utils.encoding import smart_str


# -------- 共通：集計ヘルパ（必ず F("field") を使う） --------
def _aggregate(qs):
    return qs.aggregate(
        n=Coalesce(Count("id"), 0),
        qty=Coalesce(Sum(F("qty")), 0),              # ← F() で実フィールドを明示
        fee=Coalesce(Sum(F("fee")), 0.0),
        tax=Coalesce(Sum(F("tax")), 0.0),
        pnl=Coalesce(Sum(F("pnl")), 0.0),
    )


# -------- 一覧ページ（検索 q 対応） --------
@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()

    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")  # ← dateではなくtrade_at
    if q:
        qs = qs.filter(ticker__icontains=q)

    agg = _aggregate(qs)

    return render(
        request,
        "realized/list.html",
        {"q": q, "trades": qs, "agg": agg},
    )


# -------- 追加（HTMX想定：テーブル断片とサマリー返却） --------
@login_required
@require_POST
def create(request):
    # 入力
    trade_at = request.POST.get("date") or timezone.now().date()  # フォームnameは date のままでOK
    side = (request.POST.get("side") or "SELL").upper()
    ticker = (request.POST.get("ticker") or "").strip()
    try:
        qty = int(request.POST.get("qty") or 0)
        price = float(request.POST.get("price") or 0)
        fee = float(request.POST.get("fee") or 0)
        tax = float(request.POST.get("tax") or 0)
    except Exception:
        return JsonResponse({"ok": False, "error": "数値の形式が不正です"}, status=400)
    memo = request.POST.get("memo") or ""

    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)

    # pnl：SELL=正、BUY=負方向の単票実現額
    sign = 1 if side == "SELL" else -1
    pnl = sign * qty * price - fee - tax

    RealizedTrade.objects.create(
        trade_at=trade_at,  # ← フィールド名修正
        side=side,
        ticker=ticker,
        qty=qty,
        price=price,
        fee=fee,
        tax=tax,
        pnl=pnl,
        memo=memo,
    )

    # 追加後の断片を返す（検索語は維持）
    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    agg = _aggregate(qs)
    table_html = render_to_string("realized/_table.html", {"trades": qs}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg}, request=request)

    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})


# -------- 削除（HTMXで一覧差し替え） --------
@login_required
@require_POST
def delete(request, pk: int):
    RealizedTrade.objects.filter(pk=pk).delete()

    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    agg = _aggregate(qs)
    table_html = render_to_string("realized/_table.html", {"trades": qs}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg}, request=request)

    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})


# -------- CSV エクスポート --------
@login_required
@require_GET
def export_csv(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    w = csv.writer(resp)
    w.writerow(["date", "ticker", "side", "qty", "price", "fee", "tax", "pnl", "memo"])
    for t in qs:
        w.writerow([
            t.trade_at,  # ← dateでなくtrade_at
            t.ticker, t.side, t.qty, t.price, t.fee, t.tax, t.pnl,
            smart_str(t.memo or ""),
        ])
    return resp


# -------- 部分テンプレ：テーブルだけ --------
@login_required
@require_GET
def table_partial(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    return render(request, "realized/_table.html", {"trades": qs})


# -------- 部分テンプレ：サマリーだけ --------
@login_required
@require_GET
def summary_partial(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    agg = _aggregate(qs)
    return render(request, "realized/_summary.html", {"agg": agg, "q": q})