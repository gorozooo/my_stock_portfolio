# portfolio/views_dividend.py
from calendar import monthrange

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from ..forms import DividendForm, _normalize_code_head
from ..models import Dividend
from ..services import tickers as svc_tickers
from ..services import trend as svc_trend
from ..services import dividends as svc_div  # ★ 集計系はサービスに集約


# ===== ダッシュボード（集計・可視化専用） =====
@login_required
def dashboard(request):
    """
    /dividends/dashboard/
    KPI、月次推移、証券会社別、トップ銘柄などの俯瞰画面。
    year / broker / account を受けて集計。
    """
    # パラメータ
    try:
        year = int(request.GET.get("year", timezone.localdate().year))
    except Exception:
        year = timezone.localdate().year
    broker  = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()

    # ベースQS（ログインユーザーのみ）
    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(base_qs, year=year, broker=broker, account=account)

    # 集計
    kpi        = svc_div.sum_kpis(qs)                     # gross / net / tax / count / yield_pct
    monthly    = svc_div.group_by_month(qs)               # 1..12 のリスト
    by_broker  = svc_div.group_by_broker(qs)              # [{"broker":..., "net":...}, ...]
    top_symbols= svc_div.top_symbols(qs, n=10)            # [{"label":..., "net":...}, ...]

    # 年の候補（±4年）
    cur_y = timezone.localdate().year
    year_options = [cur_y - 4 + i for i in range(9)]

    ctx = {
        "flt": {"year": year, "broker": broker, "account": account},
        "year_options": year_options,
        "kpi": kpi,
        "monthly": monthly,
        "by_broker": by_broker,
        "top_symbols": top_symbols,
        "BROKERS": getattr(Dividend, "BROKER_CHOICES", []),
        "ACCOUNTS": getattr(Dividend, "ACCOUNT_CHOICES", []),
        "urls": {"list": "dividend_list"},
    }
    return render(request, "dividends/dashboard.html", ctx)

@login_required
def dashboard_json(request):
    """GET /dividends/dashboard.json?year=YYYY&broker=...&account=..."""
    try:
        year = int(request.GET.get("year", timezone.localdate().year))
    except Exception:
        year = timezone.localdate().year
    broker  = (request.GET.get("broker") or "").strip() or None
    account = (request.GET.get("account") or "").strip() or None

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(base_qs, year=year, broker=broker, account=account)
    rows = svc_div.materialize(qs)  # 1回だけ評価

    data = {
        "kpi":        svc_div.sum_kpis(rows),
        "monthly":    svc_div.group_by_month(rows),
        "by_broker":  svc_div.group_by_broker(rows),
        "by_account": svc_div.group_by_account(rows),
        "top_symbols":svc_div.top_symbols(rows, n=10),
    }
    return JsonResponse(data)


# ===== 明細（スワイプ編集/削除・軽いフィルタ） =====
@login_required
def dividend_list(request):
    """
    /dividends/?year=YYYY&month=MM&broker=...&account=...
    明細表示用。KPIは合計のみを上部に表示。
    """
    year_q  = request.GET.get("year")
    month_q = request.GET.get("month")
    broker  = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(
        base_qs,
        year=int(year_q) if (year_q and year_q.isdigit()) else None,
        month=int(month_q) if (month_q and month_q.isdigit()) else None,
        broker=broker or None,
        account=account or None,
    ).order_by("-date", "-id")

    # KPI（合計だけ）
    kpi = svc_div.sum_kpis(qs)

    ctx = {
        "items": qs,
        "total_gross": kpi["gross"],
        "total_net":   kpi["net"],
        "total_tax":   kpi["tax"],
        "flt": {"year": year_q, "month": month_q, "broker": broker, "account": account},
    }
    return render(request, "dividends/list.html", ctx)


# ===== 作成 =====
@login_required
def dividend_create(request):
    if request.method == "POST":
        form = DividendForm(request.POST, user=request.user)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.is_net = False  # amount=税引前（フォーム仕様）
            if obj.holding and obj.holding.user_id != request.user.id:
                messages.error(request, "別ユーザーの保有は選べません。")
            else:
                obj.save()
                messages.success(request, "配当を登録しました。")
                return redirect("dividend_list")
    else:
        form = DividendForm(user=request.user)

    return render(request, "dividends/form.html", {"form": form})


# ===== 編集 =====
@login_required
def dividend_edit(request, pk: int):
    obj = get_object_or_404(Dividend, pk=pk)
    if obj.holding and obj.holding.user_id != request.user.id:
        messages.error(request, "この配当は編集できません。")
        return redirect("dividend_list")

    if request.method == "POST":
        form = DividendForm(request.POST, instance=obj, user=request.user)
        if form.is_valid():
            edited = form.save(commit=False)
            edited.is_net = False  # 税引前仕様に合わせる
            edited.save()
            messages.success(request, "配当を更新しました。")
            return redirect("dividend_list")
    else:
        form = DividendForm(instance=obj, user=request.user)

    return render(request, "dividends/form.html", {"form": form})


# ===== 削除（確認モーダル想定：POSTのみ削除） =====
@login_required
def dividend_delete(request, pk: int):
    obj = get_object_or_404(Dividend, pk=pk)
    if obj.holding and obj.holding.user_id != request.user.id:
        messages.error(request, "この配当は削除できません。")
        return redirect("dividend_list")

    if request.method == "POST":
        obj.delete()
        messages.success(request, "配当を削除しました。")
    else:
        messages.info(request, "削除をキャンセルしました。")
    return redirect("dividend_list")


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
    GET /dividends/lookup-name/?q=7203 → {"name": "..."}
    """
    raw = request.GET.get("q", "")
    head = _normalize_code_head(raw)
    name = _resolve_name_fallback(head, raw) if head else ""
    return JsonResponse({"name": name})