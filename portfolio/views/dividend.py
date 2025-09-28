# portfolio/views/dividend.py
from __future__ import annotations
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from ..models import Holding, Dividend
from ..forms import DividendForm

@login_required
def create(request, pk: int):
    """
    /holdings/<pk>/dividend/new から配当を記録するだけの最小ビュー。
    成功時は holding_list へ戻す（安定重視のページ遷移）。
    """
    holding = get_object_or_404(Holding, pk=pk, user=request.user)

    if request.method == "POST":
        form = DividendForm(request.POST)
        if form.is_valid():
            div: Dividend = form.save(commit=False)
            div.holding = holding
            div.save()
            messages.success(request, f"{holding.ticker} の配当を記録しました。")
            return redirect("holding_list")
    else:
        # 初期値：今日の日付
        from django.utils import timezone
        form = DividendForm(initial={"date": timezone.localdate(), "is_net": True})

    ctx = {"form": form, "holding": holding}
    return render(request, "dividends/form.html", ctx)