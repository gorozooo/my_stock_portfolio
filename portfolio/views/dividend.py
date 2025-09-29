# portfolio/views_dividend.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from ..forms import DividendForm, _normalize_code_head
from ..models import Dividend
from ..services import tickers as svc_tickers
from ..services import trend as svc_trend


@login_required
def dividend_list(request):
    # 自分の保有に紐づく配当 ＋（保有未選択でティッカー登録した配当）
    qs = (
        Dividend.objects.select_related("holding")
        .filter(
            Q(holding__user=request.user) |
            Q(holding__isnull=True, ticker__isnull=False)
        )
        .order_by("-date", "-id")
    )
    return render(request, "dividends/list.html", {"items": qs})


# ...（前略）
@login_required
def dividend_create(request):
    if request.method == "POST":
        form = DividendForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)

            # 保険：サーバ側でも常に税引後に固定
            obj.is_net = True

            if obj.holding and obj.holding.user_id != request.user.id:
                messages.error(request, "別ユーザーの保有は選べません。")
            else:
                obj.save()
                messages.success(request, "配当を登録しました。")
                return redirect("dividend_list")
    else:
        form = DividendForm(user=request.user)

    return render(request, "dividends/form.html", {"form": form})


# ========= 銘柄名ルックアップ API =========
def _resolve_name_fallback(code_head: str, raw: str) -> str:
    """
    HoldingForm と同等のゆるい銘柄名解決。
    4桁→tickers.csv、だめなら trend のマップ、最終的に外部取得。
    """
    name = None
    try:
        if code_head and len(code_head) == 4 and code_head.isdigit():
            name = svc_tickers.resolve_name(code_head)
    except Exception:
        pass
    if not name:
        try:
            norm = svc_trend._normalize_ticker(code_head or raw)
            name = svc_trend._lookup_name_jp_from_list(norm)
        except Exception:
            pass
    if not name:
        try:
            norm = svc_trend._normalize_ticker(code_head or raw)
            name = svc_trend._fetch_name_prefer_jp(norm)
        except Exception:
            pass
    return (name or "").strip()


@require_GET
def dividend_lookup_name(request):
    """
    GET /dividends/lookup-name/?q=7203  → {"name": "トヨタ自動車"}
    見つからなければ {"name": ""}.
    """
    raw = request.GET.get("q", "")
    head = _normalize_code_head(raw)
    name = _resolve_name_fallback(head, raw) if head else ""
    return JsonResponse({"name": name})