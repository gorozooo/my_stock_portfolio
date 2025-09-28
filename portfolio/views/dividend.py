# portfolio/views/dividend.py
from __future__ import annotations
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render, redirect

from ..models import Dividend
from ..forms import DividendForm


@login_required
def dividend_list(request):
    qs = Dividend.objects.select_related("holding")
    # Holding.user で本人分に絞る
    qs = qs.filter(holding__user=request.user).order_by("-date", "-id")
    page = Paginator(qs, 20).get_page(request.GET.get("page") or 1)
    return render(request, "dividends/list.html", {"page": page})


@login_required
def dividend_create(request):
    if request.method == "POST":
        form = DividendForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save()
            messages.success(request, "配当を保存しました。")
            return redirect("dividend_list")
    else:
        form = DividendForm(user=request.user)
    return render(request, "dividends/form.html", {"form": form, "mode": "create"})