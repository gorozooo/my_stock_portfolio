# portfolio/views/realized.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from ..models import RealizedTrade
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_POST
from django.utils.encoding import smart_str
import csv
from django.db.models import Sum, Count, F, FloatField
from django.db.models.functions import Coalesce


# ---- 既存 list_page の末尾で集計を渡すように（検索クエリq対応）----
@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-date", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    # 集計
    agg = qs.aggregate(
        n=Count("id"),
        qty=Coalesce(Sum("qty"), 0),
        fee=Coalesce(Sum("fee"), 0.0),
        tax=Coalesce(Sum("tax"), 0.0),
        pnl=Coalesce(Sum("pnl"), 0.0),
    )
    return render(request, "realized/list.html", {"q": q, "trades": qs, "agg": agg})

# ---- 作成（HTMXで返すHTMLはテーブル断片） ----
@login_required
@require_POST
def create(request):
    # 必要パラメータ（name 属性は後述のフォームと合わせる）
    date = request.POST.get("date") or timezone.now().date()
    side = (request.POST.get("side") or "SELL").upper()
    ticker = (request.POST.get("ticker") or "").strip()
    qty = int(request.POST.get("qty") or 0)
    price = float(request.POST.get("price") or 0)
    fee = float(request.POST.get("fee") or 0)
    tax = float(request.POST.get("tax") or 0)
    memo = request.POST.get("memo") or ""

    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)

    # pnl は単票の実現額（SELL 正なら利益 / BUY はマイナス想定でもOK）
    sign = 1 if side == "SELL" else -1
    pnl = sign * qty * price - fee - tax

    obj = RealizedTrade.objects.create(
        date=date, side=side, ticker=ticker, qty=qty, price=price,
        fee=fee, tax=tax, pnl=pnl, memo=memo
    )

    # 追加後のテーブル断片とサマリーを返す
    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-date", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    agg = qs.aggregate(
        n=Count("id"),
        qty=Coalesce(Sum("qty"), 0),
        fee=Coalesce(Sum("fee"), 0.0),
        tax=Coalesce(Sum("tax"), 0.0),
        pnl=Coalesce(Sum("pnl"), 0.0),
    )
    html = render_to_string("realized/_table.html", {"trades": qs}, request=request)
    summary = render_to_string("realized/_summary.html", {"agg": agg}, request=request)
    return JsonResponse({"ok": True, "table": html, "summary": summary})

# ---- 削除（HTMX で行だけ差し替え） ----
@login_required
@require_POST
def delete(request, pk: int):
    RealizedTrade.objects.filter(pk=pk).delete()
    # 現状のテーブルを返す（クエリ維持）
    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-date", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    agg = qs.aggregate(
        n=Count("id"),
        qty=Coalesce(Sum("qty"), 0),
        fee=Coalesce(Sum("fee"), 0.0),
        tax=Coalesce(Sum("tax"), 0.0),
        pnl=Coalesce(Sum("pnl"), 0.0),
    )
    html = render_to_string("realized/_table.html", {"trades": qs}, request=request)
    summary = render_to_string("realized/_summary.html", {"agg": agg}, request=request)
    return JsonResponse({"ok": True, "table": html, "summary": summary})
    
    # ---- CSV エクスポート ----
def export_csv(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-date", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    writer = csv.writer(resp)
    writer.writerow(["date","ticker","side","qty","price","fee","tax","pnl","memo"])
    for t in qs:
        writer.writerow([t.date, t.ticker, t.side, t.qty, t.price, t.fee, t.tax, t.pnl, smart_str(t.memo or "")])
    return resp

# --- 部分テンプレ：テーブルだけ返す ---
def table_partial(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-date", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    return render(request, "realized/_table.html", {"trades": qs})

# --- （必要なら）サマリーだけ返す ---
def summary_partial(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-date", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    agg = qs.aggregate(
        n=Count("id"),
        qty=Coalesce(Sum("qty"), 0),
        fee=Coalesce(Sum("fee"), 0.0),
        tax=Coalesce(Sum("tax"), 0.0),
        pnl=Coalesce(Sum("pnl"), 0.0),
    )
    return render(request, "realized/_summary.html", {"agg": agg, "q": q})
