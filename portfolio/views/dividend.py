# portfolio/views_dividend.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q, Sum, F
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from calendar import monthrange

from ..forms import DividendForm, _normalize_code_head
from ..models import Dividend
from ..services import tickers as svc_tickers
from ..services import trend as svc_trend

@login_required
def dashboard(request):
    """配当ダッシュボード：KPI / 月次推移 / 証券会社別 / トップ配当銘柄"""
    qs = (
        Dividend.objects.select_related("holding")
        .filter(Q(holding__user=request.user) | Q(holding__isnull=True, ticker__isnull=False))
    )

    # 期間（デフォ: 今年）
    try:
        year = int(request.GET.get("year", timezone.localdate().year))
    except Exception:
        year = timezone.localdate().year
    qs_year = qs.filter(date__year=year)

    # --- KPI ---
    total_cnt  = qs_year.count()
    total_tax  = float(qs_year.aggregate(s=Sum("tax"))["s"] or 0)
    # 税引前/税引後（is_net=False=税引前、True=税引後保存）に関わらず計算
    gross_sum = 0.0
    net_sum   = 0.0
    for d in qs_year:
        try:
            gross_sum += float(d.gross_amount() or 0)
            net_sum   += float(d.net_amount() or 0)
        except Exception:
            pass

    # --- 月次推移（1〜12月） ---
    monthly = []
    for m in range(1, 13):
        m_qs = qs_year.filter(date__month=m)
        g = n = t = 0.0
        for d in m_qs:
            g += float(d.gross_amount() or 0)
            n += float(d.net_amount() or 0)
            t += float(d.tax or 0)
        monthly.append({"m": m, "gross": round(g,2), "net": round(n,2), "tax": round(t,2)})

    # --- 証券会社別（税引後合計） ---
    by_broker = {}
    for d in qs_year:
        broker = (d.broker or (d.holding.broker if d.holding else "") or "OTHER")
        by_broker.setdefault(broker, 0.0)
        by_broker[broker] += float(d.net_amount() or 0)
    broker_rows = sorted(
        [{"broker": k, "net": round(v,2)} for k,v in by_broker.items()],
        key=lambda x: x["net"], reverse=True
    )

    # --- トップ配当銘柄（税引後合計 TOP10） ---
    by_symbol = {}
    for d in qs_year:
        key = d.display_ticker or d.display_name or "—"
        by_symbol.setdefault(key, 0.0)
        by_symbol[key] += float(d.net_amount() or 0)
    top_symbols = sorted(
        [{"label": k, "net": round(v,2)} for k,v in by_symbol.items()],
        key=lambda x: x["net"], reverse=True
    )[:10]

    # --- 利回り（可用データのみで概算 = 年間税引後 / 元本） ---
    # 元本 = quantity*purchase_price（無ければ holding の quantity/avg_cost を補完）
    cost_sum = 0.0
    for d in qs_year:
        qty = d.quantity or (d.holding.quantity if d.holding and d.holding.quantity else None)
        pp  = d.purchase_price or (d.holding.avg_cost if d.holding and d.holding.avg_cost is not None else None)
        if qty and pp is not None:
            cost_sum += float(qty) * float(pp)
    yield_pct = (net_sum / cost_sum * 100.0) if cost_sum > 0 else 0.0

    ctx = {
        "year": year,
        "kpi": {
            "count": total_cnt,
            "gross": round(gross_sum,2),
            "net": round(net_sum,2),
            "tax": round(total_tax,2),
            "yield_pct": round(yield_pct, 2),
        },
        "monthly": monthly,
        "by_broker": broker_rows,
        "top_symbols": top_symbols,
    }
    return render(request, "dividends/dashboard.html", ctx)


@login_required
def dividend_list(request):
    qs = (
        Dividend.objects.select_related("holding")
        .filter(
            Q(holding__user=request.user) |
            Q(holding__isnull=True, ticker__isnull=False)
        )
        .order_by("-date", "-id")
    )

    total_gross = 0
    total_net = 0
    total_tax = 0
    for it in qs:
        try:
            total_gross += float(it.gross_amount())
            total_net   += float(it.net_amount())
            total_tax   += float(it.tax or 0)
        except Exception:
            pass

    ctx = {
        "items": qs,
        "total_gross": total_gross,
        "total_net": total_net,
        "total_tax": total_tax,
    }
    return render(request, "dividends/list.html", ctx)


@login_required
def dividend_create(request):
    if request.method == "POST":
        form = DividendForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.is_net = False  # amount=税引前
            if obj.holding and obj.holding.user_id != request.user.id:
                messages.error(request, "別ユーザーの保有は選べません。")
            else:
                obj.save()
                messages.success(request, "配当を登録しました。")
                return redirect("dividend_list")
    else:
        form = DividendForm(user=request.user)

    return render(request, "dividends/form.html", {"form": form})


@login_required
def dividend_edit(request, pk: int):
    obj = get_object_or_404(Dividend, pk=pk)
    if obj.holding and obj.holding.user_id != request.user.id:
        messages.error(request, "この配当は編集できません。")
        return redirect("dividend_list")

    if request.method == "POST":
        form = DividendForm(request.POST, instance=obj, user=request.user)
        if form.is_valid():
            edited = form.save(commit=False)
            edited.is_net = False
            edited.save()
            messages.success(request, "配当を更新しました。")
            return redirect("dividend_list")
    else:
        form = DividendForm(instance=obj, user=request.user)

    return render(request, "dividends/form.html", {"form": form})


@login_required
def dividend_delete(request, pk: int):
    """一覧ページの確認モーダルからのPOSTのみで削除する。GETは一覧へ戻す。"""
    obj = get_object_or_404(Dividend, pk=pk)
    if obj.holding and obj.holding.user_id != request.user.id:
        messages.error(request, "この配当は削除できません。")
        return redirect("dividend_list")

    if request.method == "POST":
        obj.delete()
        messages.success(request, "配当を削除しました。")
    else:
        messages.info(request, "削除をキャンセルしました。")
    return redirect("dividend_list")


# ========= 銘柄名ルックアップ API =========
def _resolve_name_fallback(code_head: str, raw: str) -> str:
    name = None
    try:
        if code_head and len(code_head) == 4 and code_head.isdigit():
            name = svc_tickers.resolve_name(code_head)
    except Exception:
        pass
    if not name:
        try:
            norm = svc_trend._normalize_ticker(code_head or raw)
            name = svc_trend._lookup_name_jp_from_list(norm)
        except Exception:
            pass
    if not name:
        try:
            norm = svc_trend._normalize_ticker(code_head or raw)
            name = svc_trend._fetch_name_prefer_jp(norm)
        except Exception:
            pass
    return (name or "").strip()


@require_GET
def dividend_lookup_name(request):
    raw = request.GET.get("q", "")
    head = _normalize_code_head(raw)
    name = _resolve_name_fallback(head, raw) if head else ""
    return JsonResponse({"name": name})