# portfolio/views/holding.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.conf import settings

from ..models import Holding
from ..forms import HoldingForm
from ..services import trend as svc_trend


# -------------------------------------------------------------
# コード → 銘柄名 ルックアップAPI
#  - まず settings.TSE_NAME_OVERRIDES を優先
#  - 次に trend 側の JSON/CSV マップ
#  - 最後に yfinance の名称（外部到達時）
#  - コードは '.T' を外したヘッド（例: '167A', '7011'）で返す
# -------------------------------------------------------------
@login_required
def api_ticker_name(request):
    raw = (request.GET.get("code") or request.GET.get("q") or "").strip()
    norm = svc_trend._normalize_ticker(raw)                    # 例: '167A' -> '167A.T'
    code = (norm.split(".", 1)[0] if norm else raw).upper()    # 例: '167A'

    # 0) 上書き辞書（任意の表記で固定したい時用）
    override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get(code)
    if override:
        return JsonResponse({"code": code, "name": override})

    # 1) JSON/CSV マップ（tse_list.json/csv）
    name = svc_trend._lookup_name_jp_from_list(norm) or ""

    # 2) フォールバック: yfinance（英名になる可能性あり）
    if not name:
        try:
            name = svc_trend._fetch_name_prefer_jp(norm) or ""
        except Exception:
            name = ""

    return JsonResponse({"code": code, "name": name})


# -------------------------------------------------------------
# 保有一覧
# -------------------------------------------------------------
@login_required
def holding_list(request):
    holdings = (
        Holding.objects
        .filter(user=request.user)
        .order_by("-opened_at", "-updated_at", "-id")
    )
    return render(request, "holdings/list.html", {"holdings": holdings})


# -------------------------------------------------------------
# 保有作成
# -------------------------------------------------------------
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


# -------------------------------------------------------------
# 保有編集
# -------------------------------------------------------------
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


# -------------------------------------------------------------
# 保有削除（HTMX/通常POST両対応）
# -------------------------------------------------------------
@login_required
@require_POST
def holding_delete(request, pk: int):
    filters = {"pk": pk}
    if any(f.name == "user" for f in Holding._meta.fields):
        filters["user"] = request.user
    h = get_object_or_404(Holding, **filters)
    h.delete()
    # HTMX：対象DOMを消すだけ
    if request.headers.get("HX-Request") == "true":
        return HttpResponse("")
    return redirect("holding_list")