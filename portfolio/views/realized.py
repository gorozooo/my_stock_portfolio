# portfolio/views/realized.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from ..models import RealizedTrade

@login_required
@require_GET
def list_page(request):
    return render(request, "portfolio/realized/list.html")

@login_required
@require_GET
def table_partial(request):
    qs = RealizedTrade.objects.filter(user=request.user)
    ticker = (request.GET.get("q") or "").strip()
    if ticker:
        qs = qs.filter(ticker__icontains=ticker)
    return render(request, "portfolio/realized/_table.html", {"rows": qs[:500]})

@login_required
@require_POST
def create(request):
    try:
        trade = RealizedTrade(
            user=request.user,
            trade_at=request.POST.get("trade_at"),
            ticker=(request.POST.get("ticker") or "").upper().strip(),
            side=request.POST.get("side") or "SELL",
            qty=int(request.POST.get("qty") or 0),
            price=float(request.POST.get("price") or 0),
            fee=float(request.POST.get("fee") or 0),
            tax=float(request.POST.get("tax") or 0),
            memo=request.POST.get("memo") or "",
        )
        if trade.qty <= 0 or trade.price <= 0:
            return HttpResponseBadRequest("qty/price invalid")
        trade.save()
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

@login_required
@require_POST
def delete(request, pk: int):
    obj = get_object_or_404(RealizedTrade, pk=pk, user=request.user)
    obj.delete()
    return JsonResponse({"ok": True})