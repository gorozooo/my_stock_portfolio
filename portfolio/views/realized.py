# portfolio/views/realized.py
from __future__ import annotations
from decimal import Decimal
import csv

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import (
    Count, Sum, F, Value, Case, When, ExpressionWrapper,
    DecimalField, IntegerField
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.utils.encoding import smart_str

from ..models import Holding, RealizedTrade

DECIMAL_2 = DecimalField(max_digits=20, decimal_places=2)

def _with_pnl(qs):
    """(売値-原価)*数量 - 手数料 - 税 を Decimal で注釈"""
    fee  = Coalesce(F("fee"), Value(Decimal("0"), output_field=DECIMAL_2))
    tax  = Coalesce(F("tax"), Value(Decimal("0"), output_field=DECIMAL_2))
    basis= Coalesce(F("basis"), Value(Decimal("0"), output_field=DECIMAL_2))
    gross= (F("price") - basis) * F("qty")  # 原価控除後の粗利

    return qs.annotate(
        pnl_calc=ExpressionWrapper(
            Case(
                When(side="SELL", then=gross - fee - tax),
                # BUY は実現損益に寄与しない（0）
                default=Value(Decimal("0")),
                output_field=DECIMAL_2,
            ),
            output_field=DECIMAL_2,
        )
    )

def _aggregate(qs):
    qs = _with_pnl(qs)
    return qs.aggregate(
        n   = Coalesce(Count("id"), Value(0, output_field=IntegerField())),
        qty = Coalesce(Sum(F("qty")), Value(0, output_field=IntegerField())),
        fee = Coalesce(Sum(Coalesce(F("fee"), Value(Decimal("0"), output_field=DECIMAL_2))),
                       Value(Decimal("0"), output_field=DECIMAL_2)),
        tax = Coalesce(Sum(Coalesce(F("tax"), Value(Decimal("0"), output_field=DECIMAL_2))),
                       Value(Decimal("0"), output_field=DECIMAL_2)),
        pnl = Coalesce(Sum("pnl_calc", output_field=DECIMAL_2),
                       Value(Decimal("0"), output_field=DECIMAL_2)),
    )

@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    rows = _with_pnl(qs)
    agg  = _aggregate(qs)
    return render(request, "realized/list.html", {"q": q, "trades": rows, "agg": agg})

@login_required
@require_POST
def create(request):
    # 入力
    date_raw = (request.POST.get("date") or "").strip()
    try:
        trade_at = timezone.datetime.fromisoformat(date_raw).date() if date_raw else timezone.localdate()
    except Exception:
        trade_at = timezone.localdate()

    side   = (request.POST.get("side") or "SELL").upper()
    ticker = (request.POST.get("ticker") or "").strip()

    try:
        qty   = int(request.POST.get("qty") or 0)
        price = Decimal(str(request.POST.get("price") or "0"))
        fee   = Decimal(str(request.POST.get("fee")   or "0"))
        tax   = Decimal(str(request.POST.get("tax")   or "0"))
    except Exception:
        return JsonResponse({"ok": False, "error": "数値の形式が不正です"}, status=400)

    # basis：フォーム値が無ければ、同銘柄の保有の平均取得単価を既定に
    basis_raw = request.POST.get("basis")
    if basis_raw not in (None, ""):
        try:
            basis = Decimal(str(basis_raw))
        except Exception:
            return JsonResponse({"ok": False, "error": "原価の形式が不正です"}, status=400)
    else:
        basis = Holding.objects.filter(user=request.user, ticker=ticker)\
                .values_list("avg_cost", flat=True).first() or Decimal("0")

    memo = (request.POST.get("memo") or "").strip()

    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)

    RealizedTrade.objects.create(
        user=request.user, trade_at=trade_at, side=side, ticker=ticker,
        qty=qty, price=price, basis=basis, fee=fee, tax=tax, memo=memo,
    )

    # 再描画
    q  = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q: qs = qs.filter(ticker__icontains=q)
    rows = _with_pnl(qs); agg = _aggregate(qs)

    return JsonResponse({
        "ok": True,
        "table":   render_to_string("realized/_table.html",   {"trades": rows}, request=request),
        "summary": render_to_string("realized/_summary.html", {"agg": agg},     request=request),
    })

@login_required
@require_POST
def delete(request, pk: int):
    RealizedTrade.objects.filter(pk=pk, user=request.user).delete()

    q  = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    rows = _with_pnl(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)

    return JsonResponse({
        "ok": True,
        "table": table_html,
        "summary": summary_html
    })
    
@login_required
@require_GET
def export_csv(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q: qs = qs.filter(ticker__icontains=q)
    qs = _with_pnl(qs)

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    w = csv.writer(resp)
    w.writerow(["trade_at","ticker","side","qty","price","basis","fee","tax","pnl","memo"])
    for t in qs:
        w.writerow([t.trade_at, t.ticker, t.side, t.qty, t.price, t.basis or 0,
                    t.fee, t.tax, getattr(t, "pnl_calc", Decimal("0.00")), smart_str(t.memo or "")])
    return resp

@login_required
@require_GET
def table_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q: qs = qs.filter(ticker__icontains=q)
    rows=_with_pnl(qs)
    return render(request, "realized/_table.html", {"trades": rows})

@login_required
@require_GET
def summary_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q: qs = qs.filter(ticker__icontains=q)
    agg=_aggregate(qs)
    return render(request, "realized/_summary.html", {"agg": agg, "q": q})

# 保有 → 売却（ボトムシート）
@login_required
@require_GET
def close_sheet(request, pk: int):
    h = get_object_or_404(Holding, pk=pk, user=request.user)
    last = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at").first()
    ctx = {
        "h": h,
        "prefill": {
            "date": timezone.localdate(),
            "side": "SELL",
            "ticker": h.ticker,
            "qty": h.quantity,
            "price": "",
            "fee":  last.fee if last else 0,
            "tax":  last.tax if last else 0,
            "memo": "",
        }
    }
    return render(request, "realized/_close_sheet.html", ctx)
    
@login_required
@require_POST
@transaction.atomic
def close_submit(request, pk: int):
    h = get_object_or_404(Holding, pk=pk, user=request.user)

    # 入力
    date_raw = (request.POST.get("date") or "").strip()
    try:
        trade_at = timezone.datetime.fromisoformat(date_raw).date() if date_raw else timezone.localdate()
    except Exception:
        trade_at = timezone.localdate()

    try:
        qty   = int(request.POST.get("qty") or 0)
        price = Decimal(str(request.POST.get("price") or "0"))
        fee   = Decimal(str(request.POST.get("fee")   or "0"))
        tax   = Decimal(str(request.POST.get("tax")   or "0"))
    except Exception:
        return JsonResponse({"ok": False, "error": "数値の形式が不正です"}, status=400)

    if qty <= 0 or price <= 0 or qty > h.quantity:
        return JsonResponse({"ok": False, "error": "数量/価格を確認してください"}, status=400)

    # この時点の原価（平均取得単価）を保存
    basis = Decimal(str(h.avg_cost))

    RealizedTrade.objects.create(
        user=request.user, trade_at=trade_at, side="SELL", ticker=h.ticker, name=h.name,
        qty=qty, price=price, basis=basis, fee=fee, tax=tax,
        memo=(request.POST.get("memo") or "").strip(),
    )

    # 保有数量を更新
    h.quantity = F("quantity") - qty
    h.save(update_fields=["quantity"])
    h.refresh_from_db()
    if h.quantity <= 0:
        h.delete()

    # 最新断片を返す
    q  = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q: qs = qs.filter(ticker__icontains=q)
    rows=_with_pnl(qs); agg=_aggregate(qs)

    try:
        holdings_html = render_to_string(
            "holdings/_list.html",
            {"holdings": Holding.objects.filter(user=request.user)},
            request=request
        )
    except Exception:
        holdings_html = ""

    return JsonResponse({
        "ok": True,
        "table":   render_to_string("realized/_table.html",   {"trades": rows}, request=request),
        "summary": render_to_string("realized/_summary.html", {"agg": agg},     request=request),
        "holdings": holdings_html,
    })