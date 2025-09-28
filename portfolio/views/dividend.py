# portfolio/views/dividend.py
from __future__ import annotations
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.conf import settings

from ..forms import DividendForm  # 既存フォームを利用

@login_required
def dividend_create(request):
    if request.method == "POST":
        form = DividendForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            # モデルに user フィールドがある場合だけ代入（無ければ無視）
            if hasattr(obj, "user_id"):
                obj.user = request.user
            obj.save()
            messages.success(request, "配当を登録しました。")
            return redirect("dividend_create_done")
    else:
        # （任意）GETパラメータで初期値を入れられるように
        initial = {}
        t = (request.GET.get("ticker") or "").strip()
        n = (request.GET.get("name") or "").strip()
        if t: initial["ticker"] = t
        # stock_name / name のどちらをフォームが持っていても入るように
        if n:
            if "stock_name" in getattr(DividendForm, "base_fields", {}):
                initial["stock_name"] = n
            elif "name" in getattr(DividendForm, "base_fields", {}):
                initial["name"] = n
        form = DividendForm(initial=initial)

    return render(request, "dividends/form.html", {
        "form": form,
        "mode": "create",
    })

@login_required
def dividend_create_done(request):
    # 完了ページ（ただの静的テンプレ）
    return render(request, "dividends/done.html")