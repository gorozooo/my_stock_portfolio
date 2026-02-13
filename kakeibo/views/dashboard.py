# =========================================
# [FILE] dashboard.py
# [PATH] kakeibo/views/dashboard.py
#
# このファイルは何？
# 家計簿トップ画面（/kakeibo/）を表示するView。
# 夫婦グループのみアクセス可。
# 今月の支出合計 / カード別合計 / 立替一覧 をシンプルに出す。
# =========================================

from datetime import date
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import render

from .permissions import kakeibo_access_required
from ..models import Transaction


@login_required
def dashboard(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    today = date.today()
    month_start = today.replace(day=1)

    qs = Transaction.objects.filter(date__gte=month_start, date__lte=today)

    total_expense = qs.filter(type="EXPENSE").aggregate(s=Sum("amount"))["s"] or 0
    total_income = qs.filter(type="INCOME").aggregate(s=Sum("amount"))["s"] or 0

    # カードまとめ（Account別）
    # ✅ category は FK なので、Category.name で絞る
    card_rows = (
        qs.filter(type="EXPENSE", category__name="カードまとめ")
          .values("account__name")
          .annotate(total=Sum("amount"))
          .order_by("-total")
    )

    # 立替（一覧）
    advances = (
        qs.filter(type="EXPENSE", category__name="立替")
          .order_by("-date", "-id")[:50]
    )

    context = {
        "title": "家計簿ダッシュボード",
        "month_label": f"{today.year}-{today.month:02d}",
        "total_expense": int(total_expense),
        "total_income": int(total_income),
        "card_rows": card_rows,
        "advances": advances,
    }
    return render(request, "kakeibo/dashboard.html", context)