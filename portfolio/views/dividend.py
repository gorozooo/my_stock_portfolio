# portfolio/views/dividend.py
from __future__ import annotations
from decimal import Decimal
from datetime import date
from calendar import monthrange
from collections import defaultdict, Counter

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.utils import timezone
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.urls import reverse
import csv
from io import StringIO

from ..forms import DividendForm, _normalize_code_head
from ..models import Dividend
from ..services import tickers as svc_tickers
from ..services import trend as svc_trend
from ..services import dividends as svc_div  # 集計/目標


# ===================== 共通ユーティリティ =====================

def _parse_year(req):
    try:
        return int(req.GET.get("year") or req.GET.get("y") or timezone.localdate().year)
    except Exception:
        return timezone.localdate().year


def _flt(req):
    """共通フィルタ（year / broker / account）。"""
    return {
        "year":   req.GET.get("year") or "",
        "broker": req.GET.get("broker") or "",
        "account": req.GET.get("account") or "",
    }


def _safe_net(d) -> float:
    """Dividend の税引後金額を常に float で返す（None/例外は 0.0）。"""
    try:
        v = d.net_amount()
        return float(v or 0.0)
    except Exception:
        try:
            return float(getattr(d, "net_amount", 0.0) or 0.0)
        except Exception:
            try:
                amt = float(d.amount or 0.0)
                tax = float(d.tax or 0.0)
                return amt if getattr(d, "is_net", False) else max(0.0, amt - tax)
            except Exception:
                return 0.0


def _label_ticker(d) -> str:
    """表示用ティッカー（必ず文字列・大文字）"""
    return (getattr(d, "display_ticker", None) or getattr(d, "ticker", "") or "").upper()


def _label_name(d) -> str:
    """表示用名称（必ず文字列 / ティッカーでフォールバック）"""
    tkr = _label_ticker(d)
    return (getattr(d, "display_name", None) or getattr(d, "name", "") or tkr)


# ===================== カレンダー用ペイロード =====================

def _build_calendar_payload(user, y:int, m:int, *, broker:str|None, account:str|None):
    """
    カレンダー用の月次明細サマリを計算して返す（サーバ描画/JSON 共通）。
    """
    qs = svc_div.build_user_dividend_qs(user)
    qs = svc_div.apply_filters(qs, year=y, month=m, broker=broker or None, account=account or None)
    rows = svc_div.materialize(qs)

    last = monthrange(y, m)[1]
    days = [{"d": d, "total": 0.0, "items": []} for d in range(1, last + 1)]
    month_sum = 0.0

    broker_map  = dict(getattr(Dividend, "BROKER_CHOICES", []))
    account_map = dict(getattr(Dividend, "ACCOUNT_CHOICES", []))

    for d in rows:
        if not d.date or d.date.year != y or d.date.month != m:
            continue
        idx = d.date.day - 1
        net = float(d.net_amount() or 0.0)
        if net <= 0:
            continue

        b_code = d.broker or (d.holding.broker if getattr(d, "holding", None) else None) or "OTHER"
        a_code = d.account or (d.holding.account if getattr(d, "holding", None) else None) or "SPEC"

        month_sum += net
        days[idx]["total"] += net
        days[idx]["items"].append({
            "ticker":        d.display_ticker,
            "name":          d.display_name or d.display_ticker,
            "net":           round(net, 2),
            "broker":        b_code,
            "broker_label":  broker_map.get(b_code, b_code),
            "account":       a_code,
            "account_label": account_map.get(a_code, a_code),
        })

    for bucket in days:
        bucket["items"].sort(key=lambda x: x["net"], reverse=True)
        bucket["total"] = round(bucket["total"], 2)

    return {"year": y, "month": m, "days": days, "sum_month": round(month_sum, 2)}


# ===================== 予測（yfinance 優先 + 実月割付） =====================

def _build_forecast_payload(user, year: int, *, mode: str = "pay", stack: str = "none"):
    """
    配当予測（12ヶ月）
      - yfinance の配当履歴/直近配当を優先（fallback: DB）
      - 過去の「発生月の頻度」から、その銘柄の“よく出る月”に割付
      - mode: "pay"（支払月扱い）/ "record"（権利確定月扱い）
      - stack: "none"(合計) / "broker" / "account"
    返り値: {"months":[{"yyyymm":"YYYY-MM","values":{key:amt,...},"net":sum}], "sum12": ... , "stack":stack}
    """
    try:
        import yfinance as yf  # インストール済み想定
    except Exception:
        yf = None  # 使えなくてもフォールバックで継続

    qs = svc_div.build_user_dividend_qs(user)
    rows = svc_div.materialize(qs)

    # 株数・属性
    qty_by_symbol: dict[str, int] = {}
    broker_by_symbol: dict[str, str] = {}
    account_by_symbol: dict[str, str] = {}

    for d in rows:
        sym = _label_ticker(d)
        if not sym:
            continue

        q = 0
        try:
            q = int(d.quantity or 0)
        except Exception:
            q = 0
        if q <= 0 and getattr(d, "holding", None):
            try:
                q = int(getattr(d.holding, "quantity", 0) or 0)
            except Exception:
                q = 0
        if q <= 0:
            continue

        qty_by_symbol[sym] = q
        broker_by_symbol[sym]  = (d.broker or (d.holding.broker if getattr(d, "holding", None) else "") or "OTHER")
        account_by_symbol[sym] = (d.account or (d.holding.account if getattr(d, "holding", None) else "") or "SPEC")

    # 直近配当（1株あたり）: yfinance 優先
    last_per_share: dict[str, float] = {}
    for sym in qty_by_symbol:
        ps = None
        # yfinance
        if yf:
            try:
                tk = yf.Ticker(sym)
                divs = tk.dividends  # index は多くの銘柄で ex-div（実質「権利落ち」）※市場差異はある
                if divs is not None and getattr(divs, "empty", True) is False:
                    ps = float(divs.iloc[-1])
            except Exception:
                ps = None
        # fallback: DB
        if ps is None:
            for d in rows:
                if _label_ticker(d) == sym:
                    try:
                        tmp = d.per_share_dividend_net()
                        if tmp:
                            ps = float(tmp)
                    except Exception:
                        pass
        if ps and ps > 0:
            last_per_share[sym] = ps

    # “よく出る月”の推定：yfinance の履歴 or DB 全期間の実績から頻度上位を採用
    month_candidates: dict[str, list[int]] = {}
    for sym in qty_by_symbol:
        cnt = Counter()

        # yfinance の履歴から
        used_yf = False
        if yf:
            try:
                tk = yf.Ticker(sym)
                divs = tk.dividends
                if divs is not None and getattr(divs, "empty", True) is False:
                    # index は Timestamp。mode に応じて ex-div ＝ record と見なす
                    for ts in divs.index:
                        m = int(getattr(ts, "month", None) or ts.month)
                        cnt[m] += 1
                    used_yf = True
            except Exception:
                used_yf = False

        # フォールバック：DB 全期間
        if not used_yf:
            for d in rows:
                if _label_ticker(d) == sym and d.date:
                    m = d.date.month
                    cnt[m] += 1

        if cnt:
            # 出現回数の多い順に最大4つ
            top = [m for m, _c in cnt.most_common(4)]
            month_candidates[sym] = sorted(top)
        else:
            # 何も情報が無ければ四半期デフォルト
            month_candidates[sym] = [3, 6, 9, 12]

    # 対象年 12ヶ月の器
    yymm = [f"{year}-{m:02d}" for m in range(1, 13)]
    monthly: dict[str, dict[str, float]] = {m: {} for m in yymm}

    # 配賦
    for sym, ps in last_per_share.items():
        qty = qty_by_symbol.get(sym, 0)
        months = month_candidates.get(sym, [3, 6, 9, 12])
        if qty <= 0 or ps <= 0 or not months:
            continue

        # その銘柄の想定回数
        f = min(4, len(months))
        est_total = ps * qty * f
        each = est_total / f

        for mm in months[:f]:
            key = f"{year}-{mm:02d}"
            if stack == "broker":
                k = broker_by_symbol.get(sym, "OTHER")
            elif stack == "account":
                k = account_by_symbol.get(sym, "SPEC")
            else:
                k = "合計"
            monthly[key][k] = monthly[key].get(k, 0.0) + each

    # 整形
    months_out = []
    sum12 = 0.0
    for k in yymm:
        bucket = monthly[k]
        total = round(sum(bucket.values()), 2)
        sum12 += total
        months_out.append({"yyyymm": k, "values": bucket, "net": total})

    return {"months": months_out, "sum12": round(sum12, 2), "stack": stack, "mode": mode}


# ===================== ダッシュボード =====================

@login_required
def dashboard(request):
    try:
        year = int(request.GET.get("year", timezone.localdate().year))
    except Exception:
        year = timezone.localdate().year
    broker = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(base_qs, year=year, broker=broker or None, account=account or None)

    kpi = svc_div.sum_kpis(qs)
    monthly = svc_div.group_by_month(qs)
    by_broker = svc_div.group_by_broker(qs)
    by_account = svc_div.group_by_account(qs)
    top_symbols = svc_div.top_symbols(qs, n=10)

    goal_amount = svc_div.get_goal_amount(request.user, year)
    net_sum = Decimal(str(kpi["net"] or 0))
    goal_amount = Decimal(str(goal_amount or 0))
    progress_pct = float((net_sum / goal_amount * 100) if goal_amount > 0 else 0)
    progress_pct = round(min(100.0, max(0.0, progress_pct)), 2)
    remaining = float(max(Decimal("0"), goal_amount - net_sum))

    cur_y = timezone.localdate().year
    year_options = [cur_y - 4 + i for i in range(9)]

    ctx = {
        "flt": {"year": year, "broker": broker, "account": account},
        "year_options": year_options,
        "kpi": kpi,
        "monthly": monthly,
        "by_broker": by_broker,
        "by_account": by_account,
        "top_symbols": top_symbols,
        "goal": {
            "amount": float(goal_amount),
            "progress_pct": progress_pct,
            "remaining": remaining,
        },
        "BROKERS": getattr(Dividend, "BROKER_CHOICES", []),
        "ACCOUNTS": getattr(Dividend, "ACCOUNT_CHOICES", []),
        "urls": {"list": "dividend_list"},
    }
    return render(request, "dividends/dashboard.html", ctx)


@login_required
@require_GET
def dashboard_json(request):
    try:
        year = int(request.GET.get("year", timezone.localdate().year))
    except Exception:
        year = timezone.localdate().year
    broker = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(base_qs, year=year, broker=broker or None, account=account or None)

    kpi = svc_div.sum_kpis(qs)
    monthly = svc_div.group_by_month(qs)
    by_broker = svc_div.group_by_broker(qs)
    by_account = svc_div.group_by_account(qs)
    top_symbols = svc_div.top_symbols(qs, n=10)

    goal_amount = Decimal(str(svc_div.get_goal_amount(request.user, year) or 0))
    net_sum = Decimal(str(kpi.get("net", 0)))
    progress_pct = float((net_sum / goal_amount * 100) if goal_amount > 0 else 0)
    progress_pct = round(min(100.0, max(0.0, progress_pct)), 2)
    remaining = float(max(Decimal("0"), goal_amount - net_sum))

    data = {
        "kpi": kpi,
        "monthly": monthly,
        "by_broker": by_broker,
        "by_account": by_account,
        "top_symbols": top_symbols,
        "goal": {
            "amount": float(goal_amount),
            "progress_pct": progress_pct,
            "remaining": remaining,
        },
        "flt": {"year": year, "broker": broker, "account": account},
    }
    return JsonResponse(data)


# ===================== 目標 =====================

@login_required
@require_POST
def dividend_save_goal(request):
    try:
        year = int(request.POST.get("year") or "")
        amount = Decimal(str(request.POST.get("amount") or "0")).quantize(Decimal("0.01"))
    except Exception:
        return HttpResponseBadRequest("invalid parameters")

    svc_div.set_goal_amount(request.user, year, amount)
    messages.success(request, "年間目標を保存しました。")
    return redirect(f"{reverse('dividend_dashboard')}?year={year}")


# ===================== 明細 =====================

@login_required
def dividend_list(request):
    year_q = request.GET.get("year")
    month_q = request.GET.get("month")
    broker = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()
    q = (request.GET.get("q") or "").strip()

    year = int(year_q) if (year_q and year_q.isdigit()) else None
    month = int(month_q) if (month_q and month_q.isdigit()) else None

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(
        base_qs, year=year, month=month, broker=broker or None, account=account or None, q=q or None
    ).order_by("-date", "-id")

    kpi = svc_div.sum_kpis(qs)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    items = page_obj.object_list

    ctx = {
        "items": items,
        "page_obj": page_obj,
        "total_gross": kpi["gross"],
        "total_net": kpi["net"],
        "total_tax": kpi["tax"],
        "flt": {"year": year_q, "month": month_q, "broker": broker, "account": account, "q": q},
    }
    return render(request, "dividends/list.html", ctx)


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


# ===================== ルックアップ =====================

def _resolve_name_fallback(code_head: str, raw: str) -> str:
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
    raw = request.GET.get("q", "")
    head = _normalize_code_head(raw)
    name = _resolve_name_fallback(head, raw) if head else ""
    return JsonResponse({"name": name})


# ===================== CSV =====================

@login_required
@require_GET
def export_csv(request):
    year_q = (request.GET.get("year") or "").strip()
    month_q = (request.GET.get("month") or "").strip()
    broker = (request.GET.get("broker") or "").strip()
    account = (request.GET.get("account") or "").strip()
    q = (request.GET.get("q") or "").strip()

    year = int(year_q) if year_q.isdigit() else None
    month = int(month_q) if month_q.isdigit() else None

    base_qs = svc_div.build_user_dividend_qs(request.user)
    qs = svc_div.apply_filters(
        base_qs, year=year, month=month, broker=broker or None, account=account or None, q=q or None
    ).order_by("date", "id")

    sio = StringIO()
    writer = csv.writer(sio)
    writer.writerow(
        [
            "id","date","ticker","name","broker","account",
            "quantity","purchase_price","gross_amount","tax","net_amount","memo",
        ]
    )

    def _gross(d):
        try:
            return float(d.gross_amount())
        except Exception:
            try:
                return float(d.gross_amount)
            except Exception:
                amt = float(d.amount or 0)
                return amt if not getattr(d, "is_net", False) else (amt + float(d.tax or 0))

    def _net(d):
        try:
            return float(d.net_amount())
        except Exception:
            try:
                return float(d.net_amount)
            except Exception:
                amt = float(d.amount or 0)
                tax = float(d.tax or 0)
                return (amt - tax) if not getattr(d, "is_net", False) else amt

    for d in qs:
        writer.writerow(
            [
                d.id,
                d.date.isoformat() if d.date else "",
                _label_ticker(d),
                _label_name(d),
                (
                    d.get_broker_display()
                    if d.broker
                    else (d.holding.get_broker_display() if getattr(d, "holding", None) and d.holding.broker else "")
                ),
                (
                    d.get_account_display()
                    if d.account
                    else (d.holding.get_account_display() if getattr(d, "holding", None) and d.holding.account else "")
                ),
                d.quantity or (getattr(d.holding, "quantity", "") if getattr(d, "holding", None) else ""),
                (
                    f"{d.purchase_price:.2f}"
                    if d.purchase_price is not None
                    else (
                        f"{getattr(d.holding, 'avg_cost'):.2f}"
                        if getattr(d, "holding", None) and d.holding.avg_cost is not None
                        else ""
                    )
                ),
                f"{_gross(d):.2f}",
                f"{float(d.tax or 0):.2f}",
                f"{_net(d):.2f}",
                d.memo or "",
            ]
        )

    filename_bits = ["dividends"]
    if year_q:  filename_bits.append(str(year_q))
    if month_q: filename_bits.append(f"{int(month_q):02d}")
    filename = "_".join(filename_bits) + ".csv"

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write(sio.getvalue())
    return resp


# ===================== カレンダー =====================

@login_required
def dividends_calendar(request):
    y = int(request.GET.get("year") or timezone.localdate().year)
    m = int(request.GET.get("month") or timezone.localdate().month)
    broker = request.GET.get("broker") or None
    account = request.GET.get("account") or None

    payload = _build_calendar_payload(request.user, y, m, broker=broker, account=account)

    ctx = {
        "flt": _flt(request),
        "year_options": list(range(timezone.now().year + 1, timezone.now().year - 7, -1)),
        "month_options": list(range(1, 13)),
        "BROKERS": getattr(Dividend, "BROKER_CHOICES", []),
        "ACCOUNTS": getattr(Dividend, "ACCOUNT_CHOICES", []),
        # 初期描画用
        "days": payload["days"],
        "sum_month": payload["sum_month"],
        "year": y,
        "month": m,
        # JS でそのまま再利用できるように
        "payload_json": payload,
    }
    return render(request, "dividends/calendar.html", ctx)


@login_required
def dividends_calendar_json(request):
    y = int(request.GET.get("year") or timezone.localdate().year)
    m = int(request.GET.get("month") or timezone.localdate().month)
    broker = request.GET.get("broker") or None
    account = request.GET.get("account") or None

    payload = _build_calendar_payload(request.user, y, m, broker=broker, account=account)
    return JsonResponse(payload)


# ===================== 予測 =====================

@login_required
def dividends_forecast(request):
    y = _parse_year(request)
    mode  = (request.GET.get("mode")  or "pay").strip()       # "pay" / "record"
    stack = (request.GET.get("stack") or "none").strip()      # "none" / "broker" / "account"
    payload = _build_forecast_payload(request.user, y, mode=mode, stack=stack)
    ctx = {
        "flt": _flt(request),
        "year_options": list(range(timezone.now().year + 1, timezone.now().year - 7, -1)),
        "months": payload["months"],
        "sum12": payload["sum12"],
        "payload_json": payload,
        "year": y,
        "mode": mode,
        "stack": stack,
    }
    return render(request, "dividends/forecast.html", ctx)


@login_required
def dividends_forecast_json(request):
    y = _parse_year(request)
    mode  = (request.GET.get("mode")  or "pay").strip()
    stack = (request.GET.get("stack") or "none").strip()
    payload = _build_forecast_payload(request.user, y, mode=mode, stack=stack)
    return JsonResponse(payload)