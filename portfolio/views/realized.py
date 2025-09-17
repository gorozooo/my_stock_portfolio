# portfolio/views/realized.py
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import (
    Count, Sum, F, Value, Case, When, ExpressionWrapper,
    DecimalField, IntegerField
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import RealizedTrade
import csv
from django.utils.encoding import smart_str


# ---- PnL を Decimal で注釈 ---------------------------------------------------
DECIMAL_2 = DecimalField(max_digits=20, decimal_places=2)

def _with_pnl(qs):
    """
    行ごとの実現損益を pnl_calc として Decimal で注釈:
      SELL:  qty*price - fee - tax
      BUY : -(qty*price) - fee - tax
    """
    gross = F("qty") * F("price")                          # Decimal
    fees  = F("fee") + F("tax")                            # Decimal
    sell_expr = gross - fees                               # Decimal
    buy_expr  = -gross - fees                              # Decimal

    return qs.annotate(
        pnl_calc=ExpressionWrapper(
            Case(
                When(side="SELL", then=sell_expr),
                When(side="BUY",  then=buy_expr),
                default=Value(Decimal("0")),
                output_field=DECIMAL_2,   # Case の型も明示
            ),
            output_field=DECIMAL_2,       # 全体を Decimal に固定
        )
    )


# ---- 集計（すべて Decimal/Integer を明示） -----------------------------------
def _aggregate(qs):
    qs = _with_pnl(qs)
    return qs.aggregate(
        n   = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
        qty = Coalesce(Sum(F("qty")), Value(0), output_field=IntegerField()),
        fee = Coalesce(Sum(F("fee")), Value(Decimal("0")), output_field=DECIMAL_2),
        tax = Coalesce(Sum(F("tax")), Value(Decimal("0")), output_field=DECIMAL_2),
        pnl = Coalesce(Sum("pnl_calc", output_field=DECIMAL_2), Value(Decimal("0")), output_field=DECIMAL_2),
    )


# ---- 一覧 -------------------------------------------------------------------
@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()

    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    rows = _with_pnl(qs)   # 行別 pnl_calc を持たせる
    agg  = _aggregate(qs)  # 合計

    return render(request, "realized/list.html", {"q": q, "trades": rows, "agg": agg})


# ---- 作成（HTMX: テーブル断片とサマリーを返す） ------------------------------
@login_required
@require_POST
def create(request):
    # 入力
    trade_at = request.POST.get("date") or timezone.now().date()  # form の name は "date"
    side     = (request.POST.get("side") or "SELL").upper()
    ticker   = (request.POST.get("ticker") or "").strip()

    try:
        qty   = int(request.POST.get("qty") or 0)
        # Decimal で保持するモデルでもフォーム値は float で来ることが多い → そのまま保存OK
        price = request.POST.get("price") or "0"
        fee   = request.POST.get("fee")   or "0"
        tax   = request.POST.get("tax")   or "0"
        # バリデーション用に数値化チェックだけ軽く行う
        _ = float(price); _ = float(fee); _ = float(tax)
    except Exception:
        return JsonResponse({"ok": False, "error": "数値の形式が不正です"}, status=400)

    memo = request.POST.get("memo") or ""

    if not ticker or qty <= 0 or float(price) <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)

    RealizedTrade.objects.create(
        trade_at=trade_at, side=side, ticker=ticker,
        qty=qty, price=price, fee=fee, tax=tax, memo=memo,
    )

    # 再描画（検索語維持）
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

    qs = _with_pnl(qs)

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    w = csv.writer(resp)
    w.writerow(["date", "ticker", "side", "qty", "price", "fee", "tax", "pnl", "memo"])
    for t in qs:
        w.writerow([
            t.trade_at,
            t.ticker, t.side, t.qty, t.price, t.fee, t.tax,
            getattr(t, "pnl_calc", Decimal("0.00")),
            smart_str(t.memo or ""),
        ])
    return resp


# ---- テーブル断片 ------------------------------------------------------------
@login_required
@require_GET
def table_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    rows = _with_pnl(qs)
    return render(request, "realized/_table.html", {"trades": rows})


# ---- サマリー断片 ------------------------------------------------------------
@login_required
@require_GET
def summary_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    agg = _aggregate(qs)
    return render(request, "realized/_summary.html", {"agg": agg, "q": q})