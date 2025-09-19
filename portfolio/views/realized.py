# portfolio/views/realized.py
from __future__ import annotations

from decimal import Decimal
import csv
import logging

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import (
    Count, Sum, F, Value, Case, When, ExpressionWrapper,
    DecimalField, IntegerField, Q
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.utils.encoding import smart_str

from ..models import Holding, RealizedTrade

logger = logging.getLogger(__name__)

# ============================================================
#  ユーティリティ
# ============================================================
DEC2 = DecimalField(max_digits=20, decimal_places=2)

def _to_dec(v, default="0"):
    try:
        return Decimal(str(v if v not in (None, "") else default))
    except Exception:
        return Decimal(default)

# ============================================================
#  注釈（テーブル/サマリー兼用）
#    - cashflow_calc: 現金の受渡 (+受取/-支払)  ※税は fee に含める前提
#         SELL:  qty*price - fee
#         BUY : -(qty*price + fee)
#    - pnl_display : “投資家PnL”として画面に出す手入力の実損（= モデルの cashflow を流用）
# ============================================================
def _with_metrics(qs):
    gross = ExpressionWrapper(F("qty") * F("price"), output_field=DEC2)
    fee   = Coalesce(F("fee"), Value(Decimal("0"), output_field=DEC2))

    cashflow_calc = Case(
        When(side="SELL", then=gross - fee),
        When(side="BUY",  then=-(gross + fee)),
        default=Value(Decimal("0")),
        output_field=DEC2,
    )

    pnl_display = Coalesce(F("cashflow"), Value(Decimal("0"), output_field=DEC2))

    return qs.annotate(
        cashflow_calc=ExpressionWrapper(cashflow_calc, output_field=DEC2),
        pnl_display=ExpressionWrapper(pnl_display, output_field=DEC2),
    )

# ============================================================
#  サマリー（二軸）
#     - cash: 現金ベースの合計（受渡の積み上げ）
#     - pnl : 手入力実損（投資家PnL）の合計
# ============================================================
def _aggregate(qs):
    qs = _with_metrics(qs)
    return qs.aggregate(
        n   = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
        qty = Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),
        fee = Coalesce(
            Sum(Coalesce(F("fee"), Value(Decimal("0"), output_field=DEC2))),
            Value(Decimal("0"), output_field=DEC2)
        ),
        cash= Coalesce(Sum("cashflow_calc", output_field=DEC2), Value(Decimal("0"), output_field=DEC2)),
        pnl = Coalesce(Sum("pnl_display",   output_field=DEC2), Value(Decimal("0"), output_field=DEC2)),
    )

# ============================================================
#  画面
# ============================================================
@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_metrics(qs)
    agg  = _aggregate(qs)
    return render(request, "realized/list.html", {"q": q, "trades": rows, "agg": agg})

# ============================================================
#  作成
#   - pnl_input を “手入力の実損（投資家PnL）” として cashflow に保存
#   - fee はそのまま保存（現金計算に利用）
# ============================================================
@login_required
@require_POST
def create(request):
    date_raw = (request.POST.get("date") or "").strip()
    try:
        trade_at = timezone.datetime.fromisoformat(date_raw).date() if date_raw else timezone.localdate()
    except Exception:
        trade_at = timezone.localdate()

    ticker = (request.POST.get("ticker") or "").strip()
    name   = (request.POST.get("name")   or "").strip()
    side   = (request.POST.get("side")   or "SELL").upper()
    broker = (request.POST.get("broker") or "OTHER").upper()
    account= (request.POST.get("account") or "SPEC").upper()

    try:
        qty = int(request.POST.get("qty") or 0)
    except Exception:
        qty = 0

    price     = _to_dec(request.POST.get("price"))
    fee       = _to_dec(request.POST.get("fee"))
    pnl_input = _to_dec(request.POST.get("pnl_input"))  # ← 手入力の実損

    memo = (request.POST.get("memo") or "").strip()

    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)
    if side not in ("SELL", "BUY"):
        return JsonResponse({"ok": False, "error": "Sideが不正です"}, status=400)

    RealizedTrade.objects.create(
        user=request.user,
        trade_at=trade_at,
        side=side,
        ticker=ticker,
        name=name,
        broker=broker,
        account=account,
        qty=qty,
        price=price,
        fee=fee,
        cashflow=pnl_input,     # ← “投資家PnL”として表示・集計する値
        memo=memo,
    )

    # 再描画
    q  = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_metrics(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)
    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})

# ============================================================
#  削除（テーブル＋サマリーを同時更新して返す）
# ============================================================
@login_required
@require_POST
def delete(request, pk: int):
    RealizedTrade.objects.filter(pk=pk, user=request.user).delete()

    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_metrics(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)
    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})

# ============================================================
#  CSV（両方を出力：現金ベースと手入力PnL）
# ============================================================
@login_required
@require_GET
def export_csv(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
    qs = _with_metrics(qs)

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    w = csv.writer(resp)
    w.writerow(["trade_at", "ticker", "name", "side", "qty", "price",
                "fee", "cashflow_calc(現金)", "pnl_display(実損)", "broker", "account", "memo"])
    for t in qs:
        w.writerow([
            t.trade_at, t.ticker, smart_str(getattr(t, "name", "") or ""),
            t.side, t.qty, t.price,
            t.fee,
            getattr(t, "cashflow_calc", Decimal("0.00")),
            getattr(t, "pnl_display",  Decimal("0.00")),
            smart_str(getattr(t, "broker", "") or ""),
            smart_str(getattr(t, "account", "") or ""),
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
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
    rows = _with_metrics(qs)
    return render(request, "realized/_table.html", {"trades": rows})

@login_required
@require_GET
def summary_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
    agg = _aggregate(qs)
    return render(request, "realized/_summary.html", {"agg": agg, "q": q})

# ============================================================
#  保有 → 売却（ボトムシート／登録）
#   ※ 実損（投資家PnL）の逆算は行わず、fee は入力値を採用
#      （平均取得単価と連携しての逆算は今後対応）
# ============================================================
@login_required
@require_GET
def close_sheet(request, pk: int):
    """
    保有 → 売却のボトムシート。HTMX で素の HTML を返す。
    """
    try:
        holding_filters = {"pk": pk}
        if any(f.name == "user" for f in Holding._meta.fields):
            holding_filters["user"] = request.user
        h = get_object_or_404(Holding, **holding_filters)

        rt_qs = RealizedTrade.objects.all()
        if any(f.name == "user" for f in RealizedTrade._meta.fields):
            rt_qs = rt_qs.filter(user=request.user)
        last = rt_qs.order_by("-trade_at", "-id").first()

        def g(obj, name, default=""):
            return getattr(obj, name, default) if obj is not None else default

        h_qty = g(h, "quantity", None)
        if h_qty in (None, ""):
            h_qty = g(h, "qty", 0)

        ctx = {
            "h": h,
            "prefill": {
                "date": timezone.localdate().isoformat(),
                "side": "SELL",
                "ticker": g(h, "ticker", ""),
                "name":   g(h, "name", ""),
                "qty":    h_qty,
                "price":  "",
                "fee":    g(last, "fee", 0),
                "cashflow": g(last, "cashflow", ""),  # 手入力実損を入れる場合の初期値
                "memo":   "",
                "broker": g(last, "broker", "OTHER"),
                "account": g(last, "account", "SPEC"),
            },
        }

        html = render_to_string("realized/_close_sheet.html", ctx, request=request)
        return HttpResponse(html)

    except Exception as e:
        logger.exception("close_sheet error (pk=%s): %s", pk, e)
        import traceback
        tb = traceback.format_exc()
        error_html = f"""
        <div class="sheet" style="padding:16px">
          <div class="sheet-title" style="font-weight:700;margin-bottom:10px">クローズシートの表示に失敗しました</div>
          <div style="color:#fca5a5;margin-bottom:8px;">{str(e)}</div>
          <details style="font-size:12px;opacity:.8">
            <summary>詳細</summary>
            <pre style="white-space:pre-wrap">{tb}</pre>
          </details>
          <div style="margin-top:12px">
            <button type="button" data-dismiss="sheet"
                    style="padding:10px 12px;border:1px solid rgba(255,255,255,.2);border-radius:10px">
              閉じる
            </button>
          </div>
        </div>
        """
        return HttpResponse(error_html)

@login_required
@require_POST
@transaction.atomic
def close_submit(request, pk: int):
    """
    保有行の「売却」を登録。
    - 必須: date, qty, price
    - 任意: fee / cashflow(=手入力実損) を保存
    - ※ fee の逆算は行わない（平均取得単価と連携して後日対応）
    """
    h = get_object_or_404(Holding, pk=pk, user=request.user)

    # 入力
    date_raw = (request.POST.get("date") or "").strip()
    try:
        trade_at = timezone.datetime.fromisoformat(date_raw).date() if date_raw else timezone.localdate()
    except Exception:
        trade_at = timezone.localdate()

    side   = "SELL"
    qty    = int(request.POST.get("qty") or 0)
    price  = _to_dec(request.POST.get("price"))
    fee    = _to_dec(request.POST.get("fee"))
    # シート側で「実損」を入れてきた場合は cashflow に保存（表示・集計用）
    pnl_in = request.POST.get("cashflow")
    cashflow = None if pnl_in in (None, "") else _to_dec(pnl_in)

    broker  = (request.POST.get("broker")  or "OTHER").upper()
    account = (request.POST.get("account") or "SPEC").upper()
    memo    = (request.POST.get("memo") or "").strip()
    name    = (request.POST.get("name") or "").strip() or getattr(h, "name", "") or ""

    # 保有数量
    held_qty = getattr(h, "quantity", None)
    if held_qty is None:
        held_qty = getattr(h, "qty", 0)

    if qty <= 0 or price <= 0 or qty > held_qty:
        return JsonResponse({"ok": False, "error": "数量/価格を確認してください"}, status=400)

    # 登録
    RealizedTrade.objects.create(
        user=request.user,
        trade_at=trade_at,
        side=side,
        ticker=getattr(h, "ticker", ""),
        name=name,
        broker=broker,
        account=account,
        qty=qty,
        price=price,
        fee=fee,
        cashflow=cashflow,   # ← 手入力の実損（あれば）
        memo=memo,
    )

    # 保有数量を減算（0以下なら削除）
    if hasattr(h, "quantity"):
        h.quantity = F("quantity") - qty
        h.save(update_fields=["quantity"])
        h.refresh_from_db()
        if h.quantity <= 0:
            h.delete()
    else:
        h.qty = F("qty") - qty
        h.save(update_fields=["qty"])
        h.refresh_from_db()
        if h.qty <= 0:
            h.delete()

    # 再描画
    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_metrics(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)

    # 保有一覧（存在すれば）
    try:
        holdings_html = render_to_string(
            "holdings/_list.html",
            {"holdings": Holding.objects.filter(user=request.user)},
            request=request
        )
    except Exception:
        holdings_html = ""

    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html, "holdings": holdings_html})