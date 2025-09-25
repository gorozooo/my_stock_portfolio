# portfolio/views/holding.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.conf import settings
from datetime import date
from django.core.paginator import Paginator

from ..models import Holding
from ..forms import HoldingForm
from ..services import trend as svc_trend
from ..services.quotes import last_price

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

# 内部ユーティリティ：並び替えキーを解決
def _sort_key(item, key):
    return {
        "value": item["valuation"] or -1,
        "pnl":   item["pnl"] or -10**18,
        "days":  item["days"] or -1,
        # フォールバック（更新順）
        "updated": item["obj"].updated_at.timestamp(),
    }.get(key, item["obj"].updated_at.timestamp())

def _build_rows(qs):
    """テンプレに渡す描画用 dict のリストを作る（数式はここで計算）"""
    rows = []
    today = date.today()
    for h in qs:
        px = last_price(h.ticker)  # None 許容
        valuation = (px or 0) * (h.quantity or 0)
        pnl = ((px or 0) - float(h.avg_cost or 0)) * (h.quantity or 0)
        opened = h.opened_at or (h.created_at.date() if h.created_at else None)
        days = (today - opened).days if opened else None
        rows.append({
            "obj": h,
            "price": px,
            "valuation": valuation if px is not None else None,
            "pnl": pnl if px is not None else None,
            "days": days,
        })
    return rows

def _apply_filters(request, qs):
    broker = request.GET.get("broker") or ""
    account = request.GET.get("account") or ""
    ticker = (request.GET.get("ticker") or "").strip().upper()
    if broker:  qs = qs.filter(broker=broker)
    if account: qs = qs.filter(account=account)
    if ticker:  qs = qs.filter(ticker__icontains=ticker)
    return qs

def _render_list(request, *, template):
    qs = Holding.objects.filter(user=request.user)
    qs = _apply_filters(request, qs)

    # rows 構築
    rows = _build_rows(qs)

    # 並び替え
    sort = (request.GET.get("sort") or "").lower()   # value|pnl|days
    order = (request.GET.get("order") or "desc").lower()
    reverse = (order != "asc")
    rows.sort(key=lambda r: _sort_key(r, sort), reverse=reverse)

    # ページング（20件/頁）
    paginator = Paginator(rows, 20)
    page = paginator.get_page(request.GET.get("page") or 1)

    ctx = {
        "page": page,                 # page.object_list が rows のサブセット
        "paginator": paginator,
        "sort": sort,
        "order": order,
        "filters": {
            "broker": request.GET.get("broker",""),
            "account": request.GET.get("account",""),
            "ticker": request.GET.get("ticker",""),
        }
    }
    return render(request, template, ctx)

# -------------------------------------------------------------
# 保有一覧
# -------------------------------------------------------------
@login_required
def holding_list(request):
    # フィルタUI + 本体（_list を include）
    return _render_list(request, template="holdings/list.html")

@login_required
def holding_list_partial(request):
    # 本体のみ（HTMXで差し替え）
    return _render_list(request, template="holdings/_list.html")

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