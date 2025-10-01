from __future__ import annotations
from decimal import Decimal
from datetime import date
from calendar import monthrange

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.urls import reverse
import csv
from io import StringIO
from collections import defaultdict

from ..forms import DividendForm, _normalize_code_head
from ..models import Dividend
from ..services import tickers as svc_tickers
from ..services import trend as svc_trend
from ..services import dividends as svc_div  # 集計/目標


# ===== ダッシュボード（集計・可視化専用） =====
@login_required
def dashboard(request):
    try:
        year = int(request.GET.get("year", timezone.localdate().year))
    except Exception:
        year = timezone.localdate().year
    broker  = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(base_qs, year=year, broker=broker, account=account)

    kpi          = svc_div.sum_kpis(qs)
    monthly      = svc_div.group_by_month(qs)
    by_broker    = svc_div.group_by_broker(qs)
    by_account   = svc_div.group_by_account(qs)
    top_symbols  = svc_div.top_symbols(qs, n=10)

    goal_amount  = svc_div.get_goal_amount(request.user, year)
    net_sum      = Decimal(str(kpi["net"] or 0))
    goal_amount  = Decimal(str(goal_amount or 0))
    progress_pct = float((net_sum / goal_amount * 100) if goal_amount > 0 else 0)
    progress_pct = round(min(100.0, max(0.0, progress_pct)), 2)
    remaining    = float(max(Decimal("0"), goal_amount - net_sum))

    cur_y = timezone.localdate().year
    year_options = [cur_y - 4 + i for i in range(9)]

    ctx = {
        "flt": {"year": year, "broker": broker, "account": account},
        "year_options": year_options,
        "kpi": kpi,
        "monthly": monthly,
        "by_broker": by_broker,
        "by_account": by_account,
        "top_symbols": top_symbols,
        "goal": {
            "amount": float(goal_amount),
            "progress_pct": progress_pct,
            "remaining": remaining,
        },
        "BROKERS": getattr(Dividend, "BROKER_CHOICES", []),
        "ACCOUNTS": getattr(Dividend, "ACCOUNT_CHOICES", []),
        "urls": {"list": "dividend_list"},
    }
    return render(request, "dividends/dashboard.html", ctx)


# ===== ダッシュボード用 JSON（AJAX） =====
@login_required
@require_GET
def dashboard_json(request):
    try:
        year = int(request.GET.get("year", timezone.localdate().year))
    except Exception:
        year = timezone.localdate().year
    broker  = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(base_qs, year=year, broker=broker or None, account=account or None)

    kpi          = svc_div.sum_kpis(qs)
    monthly      = svc_div.group_by_month(qs)
    by_broker    = svc_div.group_by_broker(qs)
    by_account   = svc_div.group_by_account(qs)
    top_symbols  = svc_div.top_symbols(qs, n=10)

    goal_amount  = Decimal(str(svc_div.get_goal_amount(request.user, year) or 0))
    net_sum      = Decimal(str(kpi.get("net", 0)))
    progress_pct = float((net_sum / goal_amount * 100) if goal_amount > 0 else 0)
    progress_pct = round(min(100.0, max(0.0, progress_pct)), 2)
    remaining    = float(max(Decimal("0"), goal_amount - net_sum))

    data = {
        "kpi": kpi,
        "monthly": monthly,
        "by_broker": by_broker,
        "by_account": by_account,
        "top_symbols": top_symbols,
        "goal": {
            "amount": float(goal_amount),
            "progress_pct": progress_pct,
            "remaining": remaining,
        },
        "flt": {"year": year, "broker": broker, "account": account},
    }
    return JsonResponse(data)


# ===== 年間目標の保存（POST） =====
@login_required
@require_POST
def dividend_save_goal(request):
    try:
        year = int(request.POST.get("year") or "")
        amount = Decimal(str(request.POST.get("amount") or "0")).quantize(Decimal("0.01"))
    except Exception:
        return HttpResponseBadRequest("invalid parameters")

    svc_div.set_goal_amount(request.user, year, amount)
    messages.success(request, "年間目標を保存しました。")
    url = f"{reverse('dividend_dashboard')}?year={year}"
    return redirect(url)


# ===== 明細（スワイプ編集/削除・軽いフィルタ） =====
@login_required
def dividend_list(request):
    year_q  = request.GET.get("year")
    month_q = request.GET.get("month")
    broker  = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()
    q       = (request.GET.get("q") or "").strip()

    year  = int(year_q) if (year_q and year_q.isdigit()) else None
    month = int(month_q) if (month_q and month_q.isdigit()) else None

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(
        base_qs, year=year, month=month, broker=broker or None, account=account or None, q=q or None
    ).order_by("-date", "-id")

    kpi = svc_div.sum_kpis(qs)

    paginator = Paginator(qs, 20)
    page_obj  = paginator.get_page(request.GET.get("page") or 1)
    items     = page_obj.object_list

    ctx = {
        "items": items,
        "page_obj": page_obj,
        "total_gross": kpi["gross"],
        "total_net":   kpi["net"],
        "total_tax":   kpi["tax"],
        "flt": {"year": year_q, "month": month_q, "broker": broker, "account": account, "q": q},
    }
    return render(request, "dividends/list.html", ctx)


# ===== 作成 =====
@login_required
def dividend_create(request):
    if request.method == "POST":
        form = DividendForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.is_net = False  # amount=税引前（フォーム仕様）
            if obj.holding and obj.holding.user_id != request.user.id:
                messages.error(request, "別ユーザーの保有は選べません。")
            else:
                obj.save()
                messages.success(request, "配当を登録しました。")
                return redirect("dividend_list")
    else:
        form = DividendForm(user=request.user)
    return render(request, "dividends/form.html", {"form": form})


# ===== 編集 =====
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


# ===== 削除 =====
@login_required
def dividend_delete(request, pk: int):
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


@login_required
@require_GET
def export_csv(request):
    year_q  = (request.GET.get("year") or "").strip()
    month_q = (request.GET.get("month") or "").strip()
    broker  = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()
    q       = (request.GET.get("q") or "").strip()

    year  = int(year_q) if year_q.isdigit() else None
    month = int(month_q) if month_q.isdigit() else None

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(
        base_qs, year=year, month=month, broker=broker or None, account=account or None, q=q or None
    ).order_by("date", "id")

    sio = StringIO()
    writer = csv.writer(sio)
    writer.writerow([
        "id", "date", "ticker", "name",
        "broker", "account",
        "quantity", "purchase_price",
        "gross_amount", "tax", "net_amount",
        "memo",
    ])

    def _gross(d):
        try:
            return float(d.gross_amount())
        except Exception:
            try:
                return float(d.gross_amount)
            except Exception:
                amt = float(d.amount or 0)
                return amt if not getattr(d, "is_net", False) else (amt + float(d.tax or 0))

    def _net(d):
        try:
            return float(d.net_amount())
        except Exception:
            try:
                return float(d.net_amount)
            except Exception:
                amt = float(d.amount or 0)
                tax = float(d.tax or 0)
                return (amt - tax) if not getattr(d, "is_net", False) else amt

    for d in qs:
        writer.writerow([
            d.id,
            d.date.isoformat() if d.date else "",
            (d.display_ticker or d.ticker or ""),
            (d.display_name or d.name or ""),
            (d.get_broker_display() if d.broker else (d.holding.get_broker_display() if getattr(d, "holding", None) and d.holding.broker else "")),
            (d.get_account_display() if d.account else (d.holding.get_account_display() if getattr(d, "holding", None) and d.holding.account else "")),
            d.quantity or (getattr(d.holding, "quantity", "") if getattr(d, "holding", None) else ""),
            (f"{d.purchase_price:.2f}" if d.purchase_price is not None else (f"{getattr(d.holding, 'avg_cost'):.2f}" if getattr(d, "holding", None) and d.holding.avg_cost is not None else "")),
            f"{_gross(d):.2f}",
            f"{float(d.tax or 0):.2f}",
            f"{_net(d):.2f}",
            d.memo or "",
        ])

    filename_bits = ["dividends"]
    if year_q:  filename_bits.append(str(year_q))
    if month_q: filename_bits.append(f"{int(month_q):02d}")
    filename = "_".join(filename_bits) + ".csv"

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write(sio.getvalue())
    return resp


# ===== 共通フィルタヘルパ =====
def _parse_year(req):
    try:
        return int(req.GET.get("year") or req.GET.get("y") or timezone.now().year)
    except Exception:
        return timezone.now().year

def _flt(req):
    return {
        "year":   req.GET.get("year") or "",
        "broker": req.GET.get("broker") or "",
        "account":req.GET.get("account") or "",
    }


# ===== カレンダー（ページ） =====
@login_required
def dividends_calendar(request):
    ctx = {
        "flt": _flt(request),
        "year_options": list(range(timezone.now().year + 1, timezone.now().year - 7, -1)),
        "month_options": list(range(1, 13)),
        "BROKERS": getattr(Dividend, "BROKER_CHOICES", []),
        "ACCOUNTS": getattr(Dividend, "ACCOUNT_CHOICES", []),
    }
    return render(request, "dividends/calendar.html", ctx)


# ===== カレンダー JSON（互換キーを同梱） =====
@login_required
def dividends_calendar_json(request):
    """
    出力 days の各要素に互換キーを同梱:
      - day / d（同値）
      - sum / total（同値・小数2桁丸め）
      - list / items（同値, {ticker,name,net}）
    """
    y = int(request.GET.get("year") or timezone.now().year)
    m = int(request.GET.get("month") or timezone.now().month)
    broker  = request.GET.get("broker") or None
    account = request.GET.get("account") or None

    qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(qs, year=y, month=m, broker=broker, account=account)
    rows = svc_div.materialize(qs)

    last = monthrange(y, m)[1]
    days = [{"day": d, "d": d, "sum": 0.0, "total": 0.0, "list": [], "items": []}
            for d in range(1, last + 1)]

    month_sum = 0.0
    for rec in rows:
        if not rec.date or rec.date.year != y or rec.date.month != m:
            continue
        idx = rec.date.day - 1
        net = float(rec.net_amount() or 0.0)
        month_sum += net
        days[idx]["sum"]   += net
        days[idx]["total"] += net
        item = {
            "ticker": rec.display_ticker,
            "name":   rec.display_name or rec.display_ticker,
            "net":    round(net, 2),
        }
        days[idx]["list"].append(item)
        days[idx]["items"].append(item)

    for bucket in days:
        bucket["list"].sort(key=lambda x: x["net"], reverse=True)
        bucket["items"].sort(key=lambda x: x["net"], reverse=True)
        bucket["sum"]   = round(bucket["sum"], 2)
        bucket["total"] = round(bucket["total"], 2)

    return JsonResponse({
        "year": y,
        "month": m,
        "days": days,
        "sum_month": round(month_sum, 2),
    })


# ===== 予測ページ（ベース UI） =====
@login_required
def dividends_forecast(request):
    ctx = {
        "flt": _flt(request),
        "year_options": list(range(timezone.now().year + 1, timezone.now().year - 7, -1)),
    }
    return render(request, "dividends/forecast.html", ctx)


# ===== 予測 JSON（シンプル版） =====
@login_required
def dividends_forecast_json(request):
    """
    直近の1株配当×現在株数×想定回数の超簡易見込み。
    """
    year = _parse_year(request)

    qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(qs, year=year)
    rows = svc_div.materialize(qs)

    last_per_share = {}
    qty_by_symbol  = {}
    for d in rows:
        ps = d.per_share_dividend_net()
        if ps:
            last_per_share[d.display_ticker] = float(ps)
        q = d.quantity or (d.holding.quantity if d.holding and d.holding.quantity else 0)
        if q:
            qty_by_symbol[d.display_ticker] = int(q)

    months_by_symbol = defaultdict(set)
    for d in rows:
        months_by_symbol[d.display_ticker].add((d.date.year, d.date.month))
    freq_by_symbol = {}
    for sym, ms in months_by_symbol.items():
        cnt = len([1 for y_m in ms if y_m[0] == year])
        freq_by_symbol[sym] = 4 if cnt >= 4 else 2 if cnt >= 2 else 1

    yymm = [f"{year}-{m:02d}" for m in range(1, 13)]
    monthly = {m: 0.0 for m in yymm}
    for sym, ps in last_per_share.items():
        qty = qty_by_symbol.get(sym, 0)
        f   = freq_by_symbol.get(sym, 1)
        est_total = ps * qty * f
        each = est_total / f if f > 0 else 0.0
        for i in range(f):
            monthly[yymm[i]] += each

    out = [{"yyyymm": k, "net": round(v, 2)} for k, v in monthly.items()]
    sum12 = round(sum(monthly.values()), 2)
    return JsonResponse({"months": out, "sum12": sum12})