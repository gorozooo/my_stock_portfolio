# portfolio/views_dividend.py
from calendar import monthrange

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.core.paginator import Paginator

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
    /dividends/?year=&month=&broker=&account=&q=&page=
    明細＋簡易KPI（合計）。20件/ページ。
    """
    p = request.GET
    year   = int(p.get("year"))   if (p.get("year")  or "").isdigit() else None
    month  = int(p.get("month"))  if (p.get("month") or "").isdigit() else None
    broker = (p.get("broker") or "").strip() or None
    account= (p.get("account") or "").strip() or None
    q      = (p.get("q") or "").strip() or None

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(base_qs, year=year, month=month, broker=broker, account=account, q=q).order_by("-date","-id")

    # KPI（合計のみ）
    kpi = svc_div.sum_kpis(qs)

    # ページング
    paginator = Paginator(qs, 20)
    page_num  = int(p.get("page") or 1)
    page_obj  = paginator.get_page(page_num)

    # 次ページURL（あれば）
    next_url = None
    if page_obj.has_next():
        params = dict(p.items())
        params["page"] = str(page_obj.next_page_number())
        next_url = f"{request.path}?{urlencode(params)}"

    ctx = {
        "items": page_obj.object_list,
        "page_obj": page_obj,
        "next_url": next_url,
        "total_gross": kpi["gross"],
        "total_net":   kpi["net"],
        "total_tax":   kpi["tax"],
        "flt": {"year": year, "month": month, "broker": broker or "", "account": account or "", "q": q or ""},
    }
    return render(request, "dividends/list.html", ctx)

@login_required
def export_csv(request):
    """
    クエリ条件を反映したCSVを出力（UTF-8 BOM付き）
    """
    p = request.GET
    year   = int(p.get("year"))   if (p.get("year")  or "").isdigit() else None
    month  = int(p.get("month"))  if (p.get("month") or "").isdigit() else None
    broker = (p.get("broker") or "").strip() or None
    account= (p.get("account") or "").strip() or None
    q      = (p.get("q") or "").strip() or None

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(base_qs, year=year, month=month, broker=broker, account=account, q=q).order_by("date","id")

    # レスポンス
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="dividends.csv"'
    # Excel対策のBOM
    resp.write("\ufeff")

    writer = csv.writer(resp)
    writer.writerow(["日付","コード","銘柄名","株数","取得単価","税引前","税額","税引後","証券会社","口座区分","メモ"])
    for d in qs:
        gross = d.gross_amount() or 0
        net   = d.net_amount()   or 0
        writer.writerow([
            d.date.isoformat(),
            d.display_ticker,
            d.display_name,
            d.quantity or (d.holding.quantity if d.holding else ""),
            d.purchase_price if (d.purchase_price is not None) else (d.holding.avg_cost if d.holding else ""),
            f"{gross:.2f}",
            f"{(d.tax or 0):.2f}",
            f"{net:.2f}",
            d.broker or (d.holding.broker if d.holding else ""),
            d.account or (d.holding.account if d.holding else ""),
            d.memo or "",
        ])
    return resp


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