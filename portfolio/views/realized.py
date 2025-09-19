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
from django.db.models.functions import Coalesce, TruncMonth, TruncYear
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.utils.encoding import smart_str
from django.utils.dateparse import parse_date

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


# 期間ヘルパ
def _parse_period(request):
    """
    ?preset=THIS_MONTH|YTD|LAST_12M|THIS_YEAR|CUSTOM
    ?start=YYYY-MM-DD&end=YYYY-MM-DD （CUSTOM のときのみ）
    返り値: (start_date or None, end_date or None, preset)
    """
    preset = (request.GET.get("preset") or "THIS_MONTH").upper()
    today = timezone.localdate()

    if preset == "THIS_MONTH":
        start = today.replace(day=1)
        end = today
    elif preset == "THIS_YEAR":
        start = today.replace(month=1, day=1)
        end = today
    elif preset == "YTD":
        start = today.replace(month=1, day=1)
        end = today
    elif preset == "LAST_12M":
        # 前年同日+1で12ヶ月（ざっくり：日数は気にせず概算でOK）
        start = (today.replace(day=1) - timezone.timedelta(days=365)).replace(day=1)
        end = today
    elif preset == "CUSTOM":
        s = parse_date(request.GET.get("start") or "")
        e = parse_date(request.GET.get("end") or "")
        start = s or None
        end = e or None
    else:
        start = today.replace(day=1)
        end = today
        preset = "THIS_MONTH"

    return start, end, preset

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

    # 💰現金フロー（現物/信用で分けても式は同じにしておく）
    cashflow_calc = Case(
        When(side="SELL", then=gross - fee),
        When(side="BUY",  then=-(gross + fee)),
        default=Value(Decimal("0")),
        output_field=DEC2,
    )

    # 📈投資家PnL（手入力の実損）
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
    """
    全体集計（現物・信用を分け、合計も返す）
    """
    qs = _with_metrics(qs)

    agg = qs.aggregate(
        n   = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
        qty = Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),
        fee = Coalesce(Sum(Coalesce(F("fee"), Value(Decimal("0"), output_field=DEC2))),
                       Value(Decimal("0"), output_field=DEC2)),

        # 💰現金フロー（現物= SPEC/NISA、信用=MARGIN）
        cash_spec   = Coalesce(Sum("cashflow_calc", filter=Q(account__in=["SPEC", "NISA"]), output_field=DEC2),
                               Value(Decimal("0"), output_field=DEC2)),
        cash_margin = Coalesce(Sum("cashflow_calc", filter=Q(account="MARGIN"), output_field=DEC2),
                               Value(Decimal("0"), output_field=DEC2)),

        # 📈投資家PnL（手入力の実損の合計）
        pnl = Coalesce(Sum("pnl_display", output_field=DEC2),
                       Value(Decimal("0"), output_field=DEC2)),
    )
    # 合計現金フロー
    agg["cash_total"] = (agg["cash_spec"] or Decimal("0")) + (agg["cash_margin"] or Decimal("0"))
    return agg

def _aggregate_by_broker(qs):
    """
    証券会社別の集計（同じく現物/信用/合計とPnLを返す）
    返り値: list[dict] 例: [{"broker":"RAKUTEN", "cash_spec":..., "cash_margin":..., "cash_total":..., "pnl":...}, ...]
    """
    qs = _with_metrics(qs)

    rows = (qs
        .values("broker")
        .annotate(
            n   = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
            qty = Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),
            fee = Coalesce(Sum(Coalesce(F("fee"), Value(Decimal("0"), output_field=DEC2))),
                           Value(Decimal("0"), output_field=DEC2)),

            cash_spec   = Coalesce(Sum("cashflow_calc", filter=Q(account__in=["SPEC", "NISA"]), output_field=DEC2),
                                   Value(Decimal("0"), output_field=DEC2)),
            cash_margin = Coalesce(Sum("cashflow_calc", filter=Q(account="MARGIN"), output_field=DEC2),
                                   Value(Decimal("0"), output_field=DEC2)),
            pnl = Coalesce(Sum("pnl_display", output_field=DEC2),
                           Value(Decimal("0"), output_field=DEC2)),
        )
        .order_by("broker")
    )

    out = []
    for r in rows:
        r = dict(r)
        r["cash_total"] = (r["cash_spec"] or Decimal("0")) + (r["cash_margin"] or Decimal("0"))
        out.append(r)
    return out


@login_required
@require_GET
def summary_period_partial(request):
    """
    月次（または年次）で 📈PnL と 💰現金（現物/信用/合計）を集計して返す部分テンプレ。
    ?preset=THIS_MONTH|THIS_YEAR|YTD|LAST_12M|CUSTOM
    ?start=YYYY-MM-DD&end=YYYY-MM-DD （CUSTOMのみ）
    ?freq=month|year  （既定: month）
    """
    q = (request.GET.get("q") or "").strip()
    freq = (request.GET.get("freq") or "month").lower()
    start, end, preset = _parse_period(request)

    qs = RealizedTrade.objects.filter(user=request.user)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
    if start:
        qs = qs.filter(trade_at__gte=start)
    if end:
        qs = qs.filter(trade_at__lte=end)

    qs = _with_metrics(qs)

    # バケット化
    if freq == "year":
        bucket = TruncYear("trade_at")
        order = "period"
        label_format = "%Y"
    else:
        bucket = TruncMonth("trade_at")
        order = "period"
        label_format = "%Y-%m"

    grouped = (qs
        .annotate(period=bucket)
        .values("period")
        .annotate(
            n   = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
            qty = Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),
            fee = Coalesce(Sum(Coalesce(F("fee"), Value(Decimal("0"), output_field=DEC2))),
                           Value(Decimal("0"), output_field=DEC2)),

            cash_spec   = Coalesce(Sum("cashflow_calc", filter=Q(account__in=["SPEC", "NISA"]), output_field=DEC2),
                                   Value(Decimal("0"), output_field=DEC2)),
            cash_margin = Coalesce(Sum("cashflow_calc", filter=Q(account="MARGIN"), output_field=DEC2),
                                   Value(Decimal("0"), output_field=DEC2)),
            pnl = Coalesce(Sum("pnl_display", output_field=DEC2),
                           Value(Decimal("0"), output_field=DEC2)),
        )
        .order_by(order)
    )

    # 表示用に整形
    rows = []
    for r in grouped:
        cash_total = (r["cash_spec"] or Decimal("0")) + (r["cash_margin"] or Decimal("0"))
        rows.append({
            "period": r["period"],
            "label": r["period"].strftime(label_format) if r["period"] else "",
            "n": r["n"],
            "qty": r["qty"],
            "fee": r["fee"],
            "cash_spec": r["cash_spec"],
            "cash_margin": r["cash_margin"],
            "cash_total": cash_total,
            "pnl": r["pnl"],
        })

    ctx = {
        "rows": rows,
        "preset": preset,
        "freq": freq,
        "start": start,
        "end": end,
        "q": q,
    }
    return render(request, "realized/_summary_period.html", ctx)
    

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
    agg_brokers = _aggregate_by_broker(qs)

    return render(request, "realized/list.html", {
        "q": q,
        "trades": rows,
        "agg": agg,
        "agg_brokers": agg_brokers,   # ★ 追加
    })

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
except Exception as e:
        logger.exception("table_partial error: %s", e)
        tb = traceback.format_exc()
        html = f"""
        <div class="p-3 rounded-lg" style="background:#2b1f24;color:#ffd1d1;border:1px solid #ff9aa9;">
          <div style="font-weight:700;margin-bottom:6px">テーブル取得に失敗しました</div>
          <div style="margin-bottom:8px">{str(e)}</div>
          <details style="font-size:12px;opacity:.85"><summary>詳細</summary><pre style="white-space:pre-wrap">{tb}</pre></details>
        </div>
        """
        return HttpResponse(html, status=400)

@login_required
@require_GET
def summary_partial(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    agg  = _aggregate(qs)
    agg_brokers = _aggregate_by_broker(qs)
    return render(request, "realized/_summary.html", {"agg": agg, "agg_brokers": agg_brokers, "q": q})
except Exception as e:
        logger.exception("summary_partial error: %s", e)
        tb = traceback.format_exc()
        html = f"""
        <div class="p-3 rounded-lg" style="background:#2b1f24;color:#ffd1d1;border:1px solid #ff9aa9;">
          <div style="font-weight:700;margin-bottom:6px">サマリー取得に失敗しました</div>
          <div style="margin-bottom:8px">{str(e)}</div>
          <details style="font-size:12px;opacity:.85"><summary>詳細</summary><pre style="white-space:pre-wrap">{tb}</pre></details>
        </div>
        """
        return HttpResponse(html, status=400)


# ============================================================
#  保有 → 売却（ボトムシート／登録）
#   ※ 実損（投資家PnL）の逆算は行わず、fee は入力値を採用
#      → いまは close_submit で basis から fee を逆算する仕様に更新済み
# ============================================================
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
            "h_qty": h_qty,  # ← テンプレから常にこれを参照
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

@login_required
@require_POST
@transaction.atomic
def close_submit(request, pk: int):
    """
    保有行の「売却」を登録（平均取得から手数料を逆算）。
    - 実損（手数料控除前）＝ cashflow（±で手入力）
    - 手数料 = (売値 − basis) × 数量 − 実損
    - basis は Holding 側の代表的フィールド名から自動検出
    失敗時は {ok:false, error:"..."} を 400 で返す。
    """
    try:
        # --- Holding (user有無の両対応) ---
        filters = {"pk": pk}
        if any(f.name == "user" for f in Holding._meta.fields):
            filters["user"] = request.user
        h = get_object_or_404(Holding, **filters)

        # --- 入力 ---
        date_raw = (request.POST.get("date") or "").strip()
        try:
            trade_at = (
                timezone.datetime.fromisoformat(date_raw).date()
                if date_raw else timezone.localdate()
            )
        except Exception:
            trade_at = timezone.localdate()

        side  = "SELL"
        try:
            qty_in = int(request.POST.get("qty") or 0)
        except Exception:
            qty_in = 0
        price       = _to_dec(request.POST.get("price"))
        cashflow_in = request.POST.get("cashflow")  # 実損（手数料控除前 / ±）
        pnl_input   = None if cashflow_in in (None, "") else _to_dec(cashflow_in)

        broker  = (request.POST.get("broker")  or "OTHER").upper()
        account = (request.POST.get("account") or "SPEC").upper()
        memo    = (request.POST.get("memo")    or "").strip()
        name    = (request.POST.get("name")    or "").strip() or getattr(h, "name", "") or ""

        # --- バリデーション（数量フィールド両対応）---
        held_qty = getattr(h, "quantity", None)
        if held_qty is None:
            held_qty = getattr(h, "qty", 0)
        if qty_in <= 0 or price <= 0 or qty_in > held_qty:
            return JsonResponse({"ok": False, "error": "数量/価格を確認してください"}, status=400)

        # --- basis(平均取得単価/1株) を検出 ---
        basis_candidates = [
            "avg_cost", "average_cost", "avg_price", "average_price",
            "basis", "cost_price", "cost_per_share", "avg", "average",
            "avg_unit_cost", "avg_purchase_price",
        ]
        basis = None
        for fname in basis_candidates:
            v = getattr(h, fname, None)
            if v not in (None, ""):
                try:
                    basis = Decimal(str(v))
                    break
                except Exception:
                    continue
        if basis is None:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "保有の平均取得単価(basis)が見つかりません。Holding に avg_cost / average_cost / basis 等のいずれかを用意してください。"
                },
                status=400,
            )

        # --- 実損が未入力なら 0 扱い ---
        if pnl_input is None:
            pnl_input = Decimal("0")

        # --- 手数料を逆算 ---
        # 実損（±） = (売値 − basis) × 数量 − fee  →  fee = (売値 − basis) × 数量 − 実損
        fee = (price - basis) * Decimal(qty_in) - pnl_input

        # --- 登録（cashflow に“実損（手数料控除前）”を保存）---
        RealizedTrade.objects.create(
            user=request.user,
            trade_at=trade_at,
            side=side,
            ticker=getattr(h, "ticker", ""),
            name=name,
            broker=broker,
            account=account,
            qty=qty_in,
            price=price,
            fee=fee,
            cashflow=pnl_input,  # ← 実損（±）
            memo=memo,
        )

        # --- 保有数量の更新（0 以下で削除）---
        if hasattr(h, "quantity"):
            h.quantity = F("quantity") - qty_in
            h.save(update_fields=["quantity"])
            h.refresh_from_db()
            if h.quantity <= 0:
                h.delete()
        else:
            h.qty = F("qty") - qty_in
            h.save(update_fields=["qty"])
            h.refresh_from_db()
            if h.qty <= 0:
                h.delete()

        # --- 再描画片 ---
        q = (request.POST.get("q") or "").strip()
        qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

        rows = _with_metrics(qs)   # ← ここを _with_metrics に統一
        agg  = _aggregate(qs)

        table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
        summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)

        # 保有一覧（存在しない場合は空文字）
        try:
            holdings_html = render_to_string(
                "holdings/_list.html",
                {"holdings": Holding.objects.filter(user=request.user)},
                request=request,
            )
        except Exception:
            holdings_html = ""

        return JsonResponse({"ok": True, "table": table_html, "summary": summary_html, "holdings": holdings_html})

    except Exception as e:
        import traceback
        return JsonResponse(
            {"ok": False, "error": str(e), "traceback": traceback.format_exc()},
            status=400,
        )