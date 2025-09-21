# portfolio/views/rankings.py
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.db.models import Sum, Q
from django.shortcuts import render
from ..models import RealizedTrade
from .realized import _with_metrics  # 使っている集計ユーティリティがあれば

@login_required
@require_GET
def realized_ranking_partial(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    # 例: ティッカー別 累計PnL 上位10件
    qs = _with_metrics(qs)
    top_pnl = (
        qs.values("ticker", "name")
          .annotate(pnl_sum=Sum("pnl_display"))
          .order_by("-pnl_sum")[:10]
    )
    return render(request, "realized/_ranking.html", {"top_pnl": top_pnl})