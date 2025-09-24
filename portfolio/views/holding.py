from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse
from django.views.decorators.http import require_POST

from ..models import Holding
from ..forms import HoldingForm

@login_required
def holding_list(request):
    holdings = Holding.objects.filter(user=request.user).order_by("-opened_at", "-updated_at", "-id")
    return render(request, "holdings/list.html", {"holdings": holdings})

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
    """保有を削除（HTMX/通常POST両対応）"""
    # user フィールド有無の両対応で安全に取得
    filters = {"pk": pk}
    if any(f.name == "user" for f in Holding._meta.fields):
        filters["user"] = request.user

    h = get_object_or_404(Holding, **filters)
    h.delete()

    # HTMX なら対象DOMを消すだけで良いので空レスポンス
    if request.headers.get("HX-Request") == "true":
        return HttpResponse("")

    return redirect("holding_list")
    