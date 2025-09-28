from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages

from ..forms import DividendForm
from ..models import Dividend

@login_required
def dividend_list(request):
    qs = Dividend.objects.filter(
        holding__user=request.user
    ) | Dividend.objects.filter(
        holding__isnull=True, ticker__isnull=False
    )
    qs = qs.order_by("-date","-id")
    return render(request, "dividends/list.html", {"items": qs})

@login_required
def dividend_create(request):
    if request.method == "POST":
        form = DividendForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            # holding選択時のユーザー一致を最低限チェック（任意）
            if obj.holding and obj.holding.user_id != request.user.id:
                messages.error(request, "別ユーザーの保有は選べません。")
            else:
                obj.save()
                messages.success(request, "配当を登録しました。")
                return redirect("dividend_list")   # ← 完了後は一覧へ
    else:
        form = DividendForm(user=request.user)

    return render(request, "dividends/form.html", {"form": form})