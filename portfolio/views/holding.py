# portfolio/views/holding.py
from datetime import date
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.conf import settings
from django.core.paginator import Paginator

from ..models import Holding
from ..forms import HoldingForm
from ..services import trend as svc_trend
from ..services.quotes import last_price


@login_required
def api_ticker_name(request):
    raw = (request.GET.get("code") or request.GET.get("q") or "").strip()
    norm = svc_trend._normalize_ticker(raw)
    code = (norm.split(".", 1)[0] if norm else raw).upper()
    override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get(code)
    if override:
        return JsonResponse({"code": code, "name": override})
    name = svc_trend._lookup_name_jp_from_list(norm) or ""
    if not name:
        try:
            name = svc_trend._fetch_name_prefer_jp(norm) or ""
        except Exception:
            name = ""
    return JsonResponse({"code": code, "name": name})


def _apply_filters(request, qs):
    broker = request.GET.get("broker") or ""
    account = request.GET.get("account") or ""
    ticker = (request.GET.get("ticker") or "").strip().upper()
    if broker:  qs = qs.filter(broker=broker)
    if account: qs = qs.filter(account=account)
    if ticker:  qs = qs.filter(ticker__icontains=ticker)
    return qs

def _build_rows(qs):
    """
    表示用データを作成。
    - valuation: 評価額
    - pnl: 含み損益
    - pnl_pct: 含み損益率（%）
    - days: 保有日数
    """
    rows = []
    today = date.today()
    for h in qs:
        px = last_price(h.ticker)  # None 許容
        qty = int(h.quantity or 0)
        avg = float(h.avg_cost or 0)
        valuation = (px or 0) * qty if px is not None else None
        pnl = ((px or 0) - avg) * qty if px is not None else None
        pnl_pct = None
        if px is not None and qty > 0 and avg > 0:
            pnl_pct = ((px - avg) / avg) * 100.0
        opened = h.opened_at or (h.created_at.date() if h.created_at else None)
        days = (today - opened).days if opened else None
        rows.append({
            "obj": h,
            "price": px,
            "valuation": valuation,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "days": days,
        })
    return rows

def _sort_key(r, key):
    if key == "value":
        return (r["valuation"] is None, r["valuation"] or 0.0)
    if key == "pnl":
        return (r["pnl"] is None, r["pnl"] or 0.0)
    if key == "days":
        return (r["days"] is None, r["days"] or 0)
    return (False, r["obj"].updated_at.timestamp())

def _render_list(request, *, template):
    qs = Holding.objects.filter(user=request.user).order_by("-opened_at", "-updated_at", "-id")
    qs = _apply_filters(request, qs)

    rows = _build_rows(qs)

    sort = (request.GET.get("sort") or "").lower()
    order = (request.GET.get("order") or "desc").lower()
    reverse = (order != "asc")
    rows.sort(key=lambda r: _sort_key(r, sort), reverse=reverse)

    paginator = Paginator(rows, 20)
    page = paginator.get_page(request.GET.get("page") or 1)

    ctx = {
        "page": page,
        "paginator": paginator,
        "sort": sort,
        "order": order,
        "filters": {
            "broker": request.GET.get("broker",""),
            "account": request.GET.get("account",""),
            "ticker": request.GET.get("ticker",""),
        }
    }
    return render(request, template, ctx)

@login_required
def holding_list(request):
    return _render_list(request, template="holdings/list.html")

@login_required
def holding_list_partial(request):
    return _render_list(request, template="holdings/_list.html")

@login_required
def holding_create(request):
    if request.method == "POST":
        form = HoldingForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.save()
            messages.success(request, "保有を登録しました。")
            return redirect("holding_list")
    else:
        form = HoldingForm()
    return render(request, "holdings/form.html", {"form": form, "mode": "create"})

@login_required
def holding_edit(request, pk):
    obj = get_object_or_404(Holding, pk=pk, user=request.user)
    if request.method == "POST":
        form = HoldingForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "保有を更新しました。")
            return redirect("holding_list")
    else:
        form = HoldingForm(instance=obj)
    return render(request, "holdings/form.html", {"form": form, "mode": "edit", "obj": obj})

@login_required
@require_POST
def holding_delete(request, pk: int):
    filters = {"pk": pk}
    if any(f.name == "user" for f in Holding._meta.fields):
        filters["user"] = request.user
    h = get_object_or_404(Holding, **filters)
    h.delete()
    if request.headers.get("HX-Request") == "true":
        return HttpResponse("")
    return redirect("holding_list")