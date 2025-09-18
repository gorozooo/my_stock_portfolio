# portfolio/views/realized.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import csv

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

# ============================================================
#  ユーティリティ
# ============================================================
DEC2 = DecimalField(max_digits=20, decimal_places=2)

def _to_dec(val: str | None, default="0"):
    try:
        return Decimal(str(val if val not in (None, "") else default))
    except (InvalidOperation, TypeError):
        return Decimal(default)

# ============================================================
#  PnL は DBに保存しない（fee を控除したトレード損益を都度計算）
#   SELL:  qty*price - fee
#   BUY : -(qty*price) - fee
#   ※ 税は扱わない前提（fee に含める運用）
# ============================================================
def _with_pnl(qs):
    gross = F("qty") * F("price")  # Decimal想定
    fee   = Coalesce(F("fee"), Value(Decimal("0"), output_field=DEC2))
    return qs.annotate(
        pnl_calc=ExpressionWrapper(
            Case(
                When(side="SELL", then=gross - fee),
                When(side="BUY",  then=-(gross) - fee),
                default=Value(Decimal("0")),
                output_field=DEC2,
            ),
            output_field=DEC2,
        )
    )

def _aggregate(qs):
    qs = _with_pnl(qs)
    return qs.aggregate(
        n   = Coalesce(Count("id"), Value(0, output_field=IntegerField())),
        qty = Coalesce(Sum(F("qty")), Value(0, output_field=IntegerField())),
        fee = Coalesce(Sum(Coalesce(F("fee"), Value(Decimal("0"), output_field=DEC2))), Value(Decimal("0"), output_field=DEC2)),
        pnl = Coalesce(Sum("pnl_calc", output_field=DEC2), Value(Decimal("0"), output_field=DEC2)),
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

    rows = _with_pnl(qs)
    agg  = _aggregate(qs)
    return render(request, "realized/list.html", {"q": q, "trades": rows, "agg": agg})

# ============================================================
#  作成（cashflow入力時は fee を逆算 / name・broker 保存）
#   ・cashflow = 受渡金額（SELLは＋、BUYは− を推奨）
#   ・SELL: cashflow = qty*price - fee  → fee = qty*price - cashflow
#   ・BUY : cashflow = -(qty*price) - fee → fee = -(qty*price) - cashflow
#   ・cashflow未入力なら、入力された fee をそのまま採用
# ============================================================
@login_required
@require_POST
def create(request):
    # --- 入力 ---
    date_raw = (request.POST.get("date") or "").strip()
    try:
        trade_at = timezone.datetime.fromisoformat(date_raw).date() if date_raw else timezone.localdate()
    except Exception:
        trade_at = timezone.localdate()

    ticker  = (request.POST.get("ticker")  or "").strip()
    name    = (request.POST.get("name")    or "").strip()
    side    = (request.POST.get("side")    or "SELL").upper()
    broker  = (request.POST.get("broker")  or "OTHER").upper()   # RAKUTEN/MATSUI/SBI/OTHER を想定
    account = (request.POST.get("account") or "SPEC").upper()    # 追加: 口座区分 SPEC/MARGIN/NISA

    try:
        qty   = int(request.POST.get("qty") or 0)
    except Exception:
        qty = 0

    price    = _to_dec(request.POST.get("price"))
    fee_in   = _to_dec(request.POST.get("fee"))
    cf_in    = request.POST.get("cashflow")
    cashflow = None if cf_in in (None, "") else _to_dec(cf_in)

    memo = (request.POST.get("memo") or "").strip()

    # --- validate ---
    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)
    if side not in ("SELL", "BUY"):
        return JsonResponse({"ok": False, "error": "Sideが不正です"}, status=400)
    if broker not in ("RAKUTEN", "MATSUI", "SBI", "OTHER"):
        broker = "OTHER"
    if account not in ("SPEC", "MARGIN", "NISA"):
        account = "SPEC"

    # --- 手数料の決定（cashflow 優先）---
    # notional = 取引金額（単価×数量）。cashflow が与えられたら
    # SELL:  cashflow = notional - fee → fee = notional - cashflow
    # BUY :  cashflow = -notional - fee → fee = -notional - cashflow
    notional = Decimal(qty) * price
    if cashflow is not None:
        fee = (notional - cashflow) if side == "SELL" else (-(notional) - cashflow)
        # 必要なら異常値ケア:
        # if fee < 0: fee = Decimal("0")
    else:
        fee = fee_in

    # --- 登録 ---
    RealizedTrade.objects.create(
        user=request.user,
        trade_at=trade_at,
        side=side,
        ticker=ticker,
        name=name,
        broker=broker,
        account=account,      # ★ 追加: 口座区分を保存
        qty=qty,
        price=price,
        fee=fee,
        cashflow=cashflow,    # null可
        memo=memo,
    )

    # --- 再描画（検索語維持：コード/名称の部分一致） ---
    q  = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_pnl(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)
    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})

# ============================================================
#  削除（テーブル＋サマリーを同時更新）
# ============================================================
@login_required
@require_POST
def delete(request, pk: int):
    RealizedTrade.objects.filter(pk=pk, user=request.user).delete()

    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_pnl(qs)
    return render(request, "realized/_table.html", {"trades": rows})

# ============================================================
#  CSV（税は出力しない／cashflowはあれば出力）
# ============================================================
@login_required
@require_GET
def export_csv(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
    qs = _with_pnl(qs)

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    w = csv.writer(resp)
    w.writerow(["trade_at", "ticker", "name", "side", "qty", "price", "fee", "cashflow", "pnl_calc", "broker", "memo"])
    for t in qs:
        w.writerow([
            t.trade_at, t.ticker, smart_str(getattr(t, "name", "") or ""),
            t.side, t.qty, t.price, t.fee,
            getattr(t, "cashflow", ""),                 # モデルにあれば
            getattr(t, "pnl_calc", Decimal("0.00")),
            smart_str(getattr(t, "broker", "") or ""),
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
    rows = _with_pnl(qs)
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


from django.forms.models import model_to_dict
import logging
logger = logging.getLogger(__name__)


@login_required
@require_GET
def close_sheet(request, pk: int):
    """
    保有 → 売却のボトムシート。
    HTMX(hx-get) で #sheetRoot に innerHTML として差し込むため、
    ここは JSON ではなく “素のHTML” を返す。
    """
    try:
        # Holding 取得（user フィールド有無に対応）
        holding_filters = {"pk": pk}
        if any(f.name == "user" for f in Holding._meta.fields):
            holding_filters["user"] = request.user
        h = get_object_or_404(Holding, **holding_filters)

        # 直近 RealizedTrade（user フィールド有無に対応）
        rt_qs = RealizedTrade.objects.all()
        if any(f.name == "user" for f in RealizedTrade._meta.fields):
            rt_qs = rt_qs.filter(user=request.user)
        last = rt_qs.order_by("-trade_at", "-id").first()

        def g(obj, name, default=""):
            return getattr(obj, name, default) if obj is not None else default

        # quantity / qty どちらでも
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
                "cashflow": g(last, "cashflow", ""),
                "memo":   "",
                "broker": g(last, "broker", "OTHER"),
                "account": g(last, "account", "SPEC"),  # SPEC/MARGIN/NISA
            },
        }

        html = render_to_string("realized/_close_sheet.html", ctx, request=request)
        return HttpResponse(html)  # ★ HTML をそのまま返す

    except Exception as e:
        # 失敗時も 200 で “エラー用の簡易シートHTML” を返す（スマホで原因を見せる）
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

    except Exception as e:
        # ログにも残しつつ、スマホでも内容が見えるようにエラー詳細を返す
        logger.exception("close_sheet error: %s", e)
        import traceback
        return JsonResponse(
            {
                "ok": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
            },
            status=400,
        )

@login_required
@require_POST
@transaction.atomic
def close_submit(request, pk: int):
    """
    保有行の「売却」を登録。
    - 必須: date, qty, price
    - 任意: fee もしくは cashflow（片方だけ or 両方。cashflow 優先で fee を逆算）
    - 追加: broker, account, memo, name
    完了後、実損テーブルとサマリー、（あれば）保有一覧断片を返す。
    """
    h = get_object_or_404(Holding, pk=pk, user=request.user)

    # 入力を回収
    date_raw = (request.POST.get("date") or "").strip()
    try:
        trade_at = timezone.datetime.fromisoformat(date_raw).date() if date_raw else timezone.localdate()
    except Exception:
        trade_at = timezone.localdate()

    side   = "SELL"
    qty_in = int(request.POST.get("qty") or 0)
    price  = _to_dec(request.POST.get("price"))
    fee_in = _to_dec(request.POST.get("fee"))
    cf_in  = request.POST.get("cashflow")
    cashflow = None if cf_in in (None, "") else _to_dec(cf_in)

    broker  = (request.POST.get("broker")  or "OTHER").upper()
    account = (request.POST.get("account") or "SPEC").upper()  # SPEC/MARGIN/NISA を想定
    memo    = (request.POST.get("memo") or "").strip()

    # 表示名（保有に name があればそれを使う。フォーム側から name 渡すならそちら優先でもOK）
    name = (request.POST.get("name") or "").strip() or getattr(h, "name", "") or ""

    # バリデーション（Holding 側のフィールド名ゆれに対応）
    held_qty = getattr(h, "quantity", None)
    if held_qty is None:
        held_qty = getattr(h, "qty", 0)

    if qty_in <= 0 or price <= 0 or qty_in > held_qty:
        return JsonResponse({"ok": False, "error": "数量/価格を確認してください"}, status=400)

    # cashflow 優先で fee を逆算（売りなので notional - cashflow = fee）
    notional = _to_dec(qty_in) * price
    if cashflow is not None:
        fee = (notional - cashflow)  # SELL
    else:
        fee = fee_in

    # 取引を登録（pnl はDBに保存しない運用。集計は注釈で算出）
    RealizedTrade.objects.create(
        user=request.user,
        trade_at=trade_at,
        side=side,
        ticker=getattr(h, "ticker", ""),
        name=name,
        broker=broker,
        account=account,   # モデルに account を追加済み想定
        qty=qty_in,
        price=price,
        fee=fee,
        cashflow=cashflow, # モデルに null 可で追加済み想定
        memo=memo,
    )

    # 保有数量を減算（0以下で削除）
    if hasattr(h, "quantity"):
        h.quantity = F("quantity") - qty_in
        h.save(update_fields=["quantity"])
        h.refresh_from_db()
        if h.quantity <= 0:
            h.delete()
    else:
        # 古いスキーマ（qty）にも対応
        h.qty = F("qty") - qty_in
        h.save(update_fields=["qty"])
        h.refresh_from_db()
        if h.qty <= 0:
            h.delete()

    # 断片の再描画（検索キーワード維持：q は ticker/name の部分一致）
    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_pnl(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)

    # 保有一覧の部分テンプレがある場合だけ返す（無くてもOK）
    try:
        holdings_html = render_to_string(
            "holdings/_list.html",
            {"holdings": Holding.objects.filter(user=request.user)},
            request=request
        )
    except Exception:
        holdings_html = ""

    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html, "holdings": holdings_html})

