# portfolio/views/realized.py
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, F, FloatField, Value, Case, When
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import RealizedTrade
import csv
from django.utils.encoding import smart_str


# ---- PnL を動的に付与するヘルパ ------------------------------------------------
def _with_pnl(qs):
    """
    行ごとの実現損益を pnl_calc として注釈:
      SELL:  qty*price - fee - tax
      BUY : -(qty*price) - fee - tax
    """
    return qs.annotate(
        pnl_calc=Case(
            When(side="SELL", then=F("qty") * F("price") - F("fee") - F("tax")),
            When(side="BUY",  then=-(F("qty") * F("price")) - F("fee") - F("tax")),
            default=Value(0.0),
            output_field=FloatField(),
        )
    )


# ---- 集計ヘルパ（F(...) を使い、pnl は注釈の合計） ---------------------------
def _aggregate(qs):
    qs = _with_pnl(qs)
    return qs.aggregate(
        n   = Coalesce(Count("id"), 0),
        qty = Coalesce(Sum(F("qty")), 0),
        fee = Coalesce(Sum(F("fee")), 0.0),
        tax = Coalesce(Sum(F("tax")), 0.0),
        pnl = Coalesce(Sum("pnl_calc"), 0.0),
    )


# ---- 一覧 -------------------------------------------------------------------
@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()

    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    # テーブルで行別の pnl を表示できるよう注釈を付けて渡す
    rows = _with_pnl(qs)
    agg  = _aggregate(qs)

    return render(request, "realized/list.html", {"q": q, "trades": rows, "agg": agg})


# ---- 作成（HTMX: テーブル断片とサマリーを返す） ------------------------------
@login_required
@require_POST
def create(request):
    # 入力
    trade_at = request.POST.get("date") or timezone.now().date()  # form の name は date のままでOK
    side     = (request.POST.get("side") or "SELL").upper()
    ticker   = (request.POST.get("ticker") or "").strip()

    try:
        qty   = int(request.POST.get("qty") or 0)
        price = float(request.POST.get("price") or 0)
        fee   = float(request.POST.get("fee") or 0)
        tax   = float(request.POST.get("tax") or 0)
    except Exception:
        return JsonResponse({"ok": False, "error": "数値の形式が不正です"}, status=400)

    memo = request.POST.get("memo") or ""

    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)

    # 保存（モデルに pnl フィールドが無いので保存しない）
    RealizedTrade.objects.create(
        trade_at=trade_at, side=side, ticker=ticker,
        qty=qty, price=price, fee=fee, tax=tax, memo=memo,
    )

    # 再描画用クエリ（検索語維持）
    q  = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    rows = _with_pnl(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)

    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})


# ---- 削除（HTMX: テーブル断片とサマリーを返す） ------------------------------
@login_required
@require_POST
def delete(request, pk: int):
    RealizedTrade.objects.filter(pk=pk).delete()

    q  = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    rows = _with_pnl(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)

    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})


# ---- CSV エクスポート --------------------------------------------------------
@login_required
@require_GET
def export_csv(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    # 行別 pnl を注釈
    qs = _with_pnl(qs)

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    w = csv.writer(resp)
    w.writerow(["date", "ticker", "side", "qty", "price", "fee", "tax", "pnl", "memo"])
    for t in qs:
        w.writerow([
            t.trade_at,
            t.ticker, t.side, t.qty, t.price, t.fee, t.tax,
            getattr(t, "pnl_calc", 0.0),
            smart_str(t.memo or ""),
        ])
    return resp


# ---- 部分テンプレ：テーブルだけ ----------------------------------------------
@login_required
@require_GET
def table_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    rows = _with_pnl(qs)
    return render(request, "realized/_table.html", {"trades": rows})


# ---- 部分テンプレ：サマリーだけ ----------------------------------------------
@login_required
@require_GET
def summary_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    agg = _aggregate(qs)
    return render(request, "realized/_summary.html", {"agg": agg, "q": q})