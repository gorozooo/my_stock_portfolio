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


# ============================================================
#  PnL は DBに保存しない。都度 Decimal で計算して注釈/集計。
# ============================================================
DECIMAL_2 = DecimalField(max_digits=20, decimal_places=2)

def _with_pnl(qs):
    """
    行ごとの実現損益を pnl_calc として Decimal で注釈:
      SELL:  qty*price - fee - tax
      BUY : -(qty*price) - fee - tax
    """
    gross = F("qty") * F("price")  # Decimal 同士の乗算を想定
    fees = (
        Coalesce(F("fee"), Value(Decimal("0"), output_field=DECIMAL_2))
        + Coalesce(F("tax"), Value(Decimal("0"), output_field=DECIMAL_2))
    )

    return qs.annotate(
        pnl_calc=ExpressionWrapper(
            Case(
                When(side="SELL", then=gross - fees),
                When(side="BUY", then=-(gross) - fees),
                default=Value(Decimal("0")),
                output_field=DECIMAL_2,
            ),
            output_field=DECIMAL_2,
        )
    )

def _aggregate(qs):
    """一覧のサマリーを Decimal/Integer を明示して取得"""
    qs = _with_pnl(qs)
    return qs.aggregate(
        n   = Coalesce(Count("id"), Value(0, output_field=IntegerField())),
        qty = Coalesce(Sum(F("qty")), Value(0, output_field=IntegerField())),
        fee = Coalesce(
            Sum(Coalesce(F("fee"), Value(Decimal("0"), output_field=DECIMAL_2))),
            Value(Decimal("0"), output_field=DECIMAL_2)
        ),
        tax = Coalesce(
            Sum(Coalesce(F("tax"), Value(Decimal("0"), output_field=DECIMAL_2))),
            Value(Decimal("0"), output_field=DECIMAL_2)
        ),
        pnl = Coalesce(
            Sum("pnl_calc", output_field=DECIMAL_2),
            Value(Decimal("0"), output_field=DECIMAL_2)
        ),
    )


# ============================================================
#  画面
# ============================================================
@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    rows = _with_pnl(qs)
    agg  = _aggregate(qs)

    return render(request, "realized/list.html", {"q": q, "trades": rows, "agg": agg})


# ============================================================
#  作成（HTMX: テーブル断片 + サマリー返却）
# ============================================================
@login_required
@require_POST
def create(request):
    # 入力取得
    date_raw = (request.POST.get("date") or "").strip()  # form name="date"
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

    memo = (request.POST.get("memo") or "").strip()

    if not ticker or qty <= 0 or price <= 0:
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


# ============================================================
#  削除（HTMX: テーブル断片 + サマリー返却）
# ============================================================
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


# ============================================================
#  CSV
# ============================================================
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
    w.writerow(["trade_at", "ticker", "side", "qty", "price", "fee", "tax", "pnl", "memo"])
    for t in qs:
        w.writerow([
            t.trade_at,
            t.ticker, t.side, t.qty, t.price, t.fee, t.tax,
            getattr(t, "pnl_calc", Decimal("0.00")),
            smart_str(t.memo or ""),
        ])
    return resp


# ============================================================
#  部分テンプレ
# ============================================================
@login_required
@require_GET
def table_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    rows = _with_pnl(qs)
    return render(request, "realized/_table.html", {"trades": rows})

@login_required
@require_GET
def summary_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)
    agg = _aggregate(qs)
    return render(request, "realized/_summary.html", {"agg": agg, "q": q})


# ============================================================
#  保有 → 売却（ボトムシート + 登録）
# ============================================================
@login_required
@require_GET
def close_sheet(request, pk: int):
    h = get_object_or_404(Holding, pk=pk, user=request.user)
    last = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at").first()
    ctx = {
        "h": h,
        "prefill": {
            "date": timezone.now().date(),
            "side": "SELL",
            "ticker": h.ticker,
            "qty": h.quantity,          # ← Holding は quantity フィールド
            "price": "",
            "fee":  last.fee if last else 0,
            "tax":  last.tax if last else 0,
            "memo": "",
        }
    }
    html = render_to_string("realized/_close_sheet.html", ctx, request=request)
    return HttpResponse(html, content_type="text/html; charset=utf-8")

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

    side = "SELL"
    try:
        qty   = int(request.POST.get("qty") or 0)
        price = Decimal(str(request.POST.get("price") or "0"))
        fee   = Decimal(str(request.POST.get("fee")   or "0"))
        tax   = Decimal(str(request.POST.get("tax")   or "0"))
    except Exception:
        return JsonResponse({"ok": False, "error": "数値の形式が不正です"}, status=400)

    memo = (request.POST.get("memo") or "").strip()

    # Holding は quantity を使用
    if qty <= 0 or price <= 0 or qty > h.quantity:
        return JsonResponse({"ok": False, "error": "数量/価格を確認してください"}, status=400)

    # 登録（pnl は保存しない）
    RealizedTrade.objects.create(
        user=request.user, trade_at=trade_at, side=side, ticker=h.ticker,
        qty=qty, price=price, fee=fee, tax=tax, memo=memo
    )

    # 保有数量を減算（0なら削除）
    h.quantity = F("quantity") - qty
    h.save(update_fields=["quantity"])
    h.refresh_from_db()
    if h.quantity <= 0:
        h.delete()

    # 最新テーブル/サマリー/（あれば）保有一覧断片を返す
    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.all().order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q)

    rows = _with_pnl(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)

    # 保有一覧の部分テンプレが存在しない環境でも落ちないように
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
        "table": table_html,
        "summary": summary_html,
        "holdings": holdings_html
    })