# =========================================================
# [FILE] demo.py
# [PATH] <project_root>/tradeai/views/demo.py
#
# このファイルは何？
# - tradeai のデモページです。
# - まだ本実装前なので、現状の位置づけと次に入る内容を正直に表示します。
# =========================================================

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from tradeai.models.demo_trade import DemoTrade


@login_required
def demo_page(request):
    user = request.user

    open_demo_count = DemoTrade.objects.filter(
        user=user,
        status=DemoTrade.StatusChoices.OPEN,
    ).count()

    total_demo_count = DemoTrade.objects.filter(user=user).count()

    context = {
        "page_title": "デモ",
        "open_demo_count": open_demo_count,
        "total_demo_count": total_demo_count,
    }
    return render(request, "tradeai/demo.html", context)