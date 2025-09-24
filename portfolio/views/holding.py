# views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, JsonResponse   # ← 追加
from django.views.decorators.http import require_POST

from ..models import Holding
from ..forms import HoldingForm
from ..services import trend as svc_trend

# 追加：コード→銘柄名の簡易API（フロントのオートフィル用）
from ..services.tickers import resolve_name as resolve_tse_name
from ..services.tickers import _normalize_to_code as normalize_code  # 既存ユーティリティ流用

def _normalize_code4(s: str) -> str:
    t = (s or "").strip().upper()
    if "." in t: t = t.split(".", 1)[0]
    return t if (len(t) == 4 and t.isdigit()) else ""

@login_required
def api_ticker_name(request):
    raw = request.GET.get("code") or request.GET.get("q") or ""
    code4 = _normalize_code4(raw)
    ticker = f"{code4}.T" if code4 else str(raw or "")
    try:
        name = svc_trend._fetch_name_prefer_jp(ticker) or ""
    except Exception:
        name = ""
    return JsonResponse({"code": code4, "name": name})

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
    filters = {"pk": pk}
    if any(f.name == "user" for f in Holding._meta.fields):
        filters["user"] = request.user
    h = get_object_or_404(Holding, **filters)
    h.delete()
    if request.headers.get("HX-Request") == "true":
        return HttpResponse("")
    return redirect("holding_list")