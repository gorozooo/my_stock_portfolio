# portfolio/views/realized.py 内でもOK
from django.db.models import Count, Sum, Avg, Case, When, IntegerField, DecimalField, Value, Q
from django.db.models.functions import Coalesce

@login_required
@require_GET
def realized_ranking_partial(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user)

    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    qs = _with_metrics(qs)

    rows = (
        qs.values("ticker", "name")
          .annotate(
              n=Count("id"),
              qty=Coalesce(Sum("qty"), Value(0)),
              pnl=Coalesce(Sum("pnl_display"), Value(0)),
              avg=Coalesce(Avg("pnl_display"), Value(0)),
              win=Coalesce(Sum(
                  Case(When(pnl_display__gt=0, then=1), default=0, output_field=IntegerField())
              ), Value(0))
          )
          .order_by("-pnl")
    )

    # 勝率パーセント換算
    for r in rows:
        r["win_rate"] = round((r["win"] / r["n"]) * 100, 1) if r["n"] > 0 else 0

    top5 = list(rows[:5])
    worst5 = list(rows.order_by("pnl")[:5])

    return render(request, "realized/_ranking.html", {
        "top5": top5,
        "worst5": worst5,
        "q": q,
    })