# portfolio/views/dividend.py
from __future__ import annotations
from decimal import Decimal
from datetime import date
from calendar import monthrange

from functools import lru_cache

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
from collections import defaultdict

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


def _label_ticker(d) -> str:
    return (getattr(d, "display_ticker", None) or getattr(d, "ticker", "") or "").upper()


def _label_name(d) -> str:
    tkr = _label_ticker(d)
    return (getattr(d, "display_name", None) or getattr(d, "name", "") or tkr)


def _is_jp_symbol(sym: str) -> bool:
    """
    日本株かどうかの緩め判定。
    - 4桁数字（例: 7272）
    - サフィックス .T / .JP / -T 等
    - 先頭が JP〜 のコード
    """
    s = (sym or "").upper()
    if len(s) == 4 and s.isdigit():
        return True
    if s.endswith(".T") or s.endswith(".JP") or s.endswith("-T"):
        return True
    if s.startswith("JP"):
        return True
    return False


def _shift_months(months: list[int], delta: int) -> list[int]:
    """
    月配列を delta ヶ月ずらす（1..12 に wrap）。
    例: 9月, delta=-3 → 6月
    """
    out = []
    for m in months:
        try:
            mi = int(m)
        except Exception:
            continue
        if 1 <= mi <= 12:
            out.append(((mi - 1 + delta) % 12) + 1)
    return sorted(out)


# ===================== yfinance (ex/pay) 補助 =====================

def _market_hint_from_symbol(sym: str) -> str:
    s = (sym or "").upper()
    if s.endswith(".T"):   return "JP"
    if s.endswith(".AX"):  return "AU"
    if s.endswith(".L"):   return "UK"
    return "US"  # 既定

@lru_cache(maxsize=512)
def _fetch_div_months_from_yf(sym: str) -> dict:
    """
    yfinance 優先で「権利確定(ex)」「支払い(pay)」月を取得して返す。
    返り値: {"ex":[1..12], "pay":[1..12]}（無い要素は空配列）
    """
    out = {"ex": [], "pay": []}
    try:
        import yfinance as yf  # 環境になければ except へ
        try:
            import pandas as pd  # to_datetime 用
        except Exception:
            pd = None

        tk = yf.Ticker(sym)

        # --- ex months ---
        ex_months: list[int] = []
        # 1) actions (新API) に Dividends 列がある場合
        try:
            acts = tk.get_actions(prepost=False)
            if acts is not None and getattr(acts, "empty", True) is False:
                col = "Dividends" if "Dividends" in acts.columns else ("dividends" if "dividends" in acts.columns else None)
                if col:
                    ex_idx = acts[acts[col] > 0].index
                    ex_months = sorted({int(d.month) for d in ex_idx})
        except Exception:
            pass

        # 2) 従来API .dividends（index=ex-date 想定）
        if not ex_months:
            divs = getattr(tk, "dividends", None)
            if divs is not None and getattr(divs, "empty", True) is False:
                ex_months = sorted({int(d.month) for d in divs.index})

        # --- payment months ---
        pay_months: list[int] = []
        try:
            df = tk.get_dividends()
            if df is not None and getattr(df, "empty", True) is False and pd is not None:
                cols = {c.lower(): c for c in df.columns}
                if "paymentdate" in cols:
                    col = cols["paymentdate"]
                    vals = [x for x in df[col].tolist() if x]
                    # to_datetime できたもののみ採用
                    parsed = []
                    for x in vals:
                        try:
                            parsed.append(pd.to_datetime(x))
                        except Exception:
                            pass
                    if parsed:
                        pay_months = sorted({int(x.month) for x in parsed})
                # exDate がここにあれば ex_months も更新
                if not ex_months and "exdate" in cols:
                    col = cols["exdate"]
                    vals = [x for x in df[col].tolist() if x]
                    parsed = []
                    for x in vals:
                        try:
                            parsed.append(pd.to_datetime(x))
                        except Exception:
                            pass
                    if parsed:
                        ex_months = sorted({int(x.month) for x in parsed})
        except Exception:
            pass

        # フォールバック: payment が空なら ex→市場ヒントで推定 (+1 US / +2 JP)
        if not pay_months and ex_months:
            hint = _market_hint_from_symbol(sym)
            delta = 2 if hint == "JP" else 1
            pay_months = sorted({((m - 1 + delta) % 12) + 1 for m in ex_months})

        out["ex"] = ex_months
        out["pay"] = pay_months
        return out
    except Exception:
        # yfinance 不在/失敗は空配列のまま返す
        return out

# ===================== カレンダー用ペイロード =====================

def _build_calendar_payload(user, y:int, m:int, *, broker:str|None, account:str|None):
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
        try:
            net = float(d.net_amount() or 0.0)
        except Exception:
            net = 0.0
        if net <= 0:
            continue

        b_code = d.broker or (d.holding.broker if getattr(d, "holding", None) else None) or "OTHER"
        a_code = d.account or (d.holding.account if getattr(d, "holding", None) else None) or "SPEC"

        month_sum += net
        days[idx]["total"] += net
        days[idx]["items"].append({
            "ticker":        _label_ticker(d),
            "name":          _label_name(d),
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


# ===================== 予測（yfinance 優先 / 支払月・権利確定月 切替） =====================

def _build_forecast_payload(user, year: int, mode: str = "pay"):
    """
    超シンプル予測（直近1株配当 × 現在株数 × 想定回数）。
    支払月は DB 実績からユニーク月を抽出してそのまま対象年へ複製。
    権利確定月は支払月を銘柄/市場ごとのオフセットでシフトして算出。
      - JP: 支払月 -3 ヶ月（例：9月支払 → 6月権利確定）
      - それ以外: 支払月 -1 ヶ月
    返り値: {"months":[{"yyyymm":"YYYY-MM","net":…},…], "sum12": …}
    """
    mode = (mode or "pay").strip().lower()
    if mode not in ("pay", "record", "ex"):
        mode = "pay"

    # 1) 元データ取得（年で制限せず全体からパターン抽出）
    base_qs = svc_div.build_user_dividend_qs(user)
    rows = svc_div.materialize(base_qs)

    # 2) 直近1株配当・株数（Dividend優先→Holding）
    last_per_share: dict[str, float] = {}
    qty_by_symbol: dict[str, int] = {}

    for d in rows:
        sym = (getattr(d, "display_ticker", None) or getattr(d, "ticker", "") or "").upper()

        # 直近1株配当（税後）
        try:
            ps = d.per_share_dividend_net()
        except Exception:
            ps = None
        if ps is not None:
            last_per_share[sym] = float(ps)  # 後勝ちで直近化

        # 株数
        q = 0
        try:
            q = int(d.quantity or 0)
        except Exception:
            q = 0
        if q <= 0 and getattr(d, "holding", None):
            try:
                q = int(d.holding.quantity or 0)
            except Exception:
                q = 0
        if q > 0:
            qty_by_symbol[sym] = q

    # 3) 支払月の抽出（全期間の実績からユニーク月を採用）
    from collections import defaultdict
    tmp: dict[str, set[int]] = defaultdict(set)
    for d in rows:
        if not getattr(d, "date", None):
            continue
        sym = (getattr(d, "display_ticker", None) or getattr(d, "ticker", "") or "").upper()
        try:
            mm = int(d.date.month)
            if 1 <= mm <= 12:
                tmp[sym].add(mm)
        except Exception:
            pass

    pay_months_by_symbol: dict[str, list[int]] = {}
    for sym, ms in tmp.items():
        pay_months_by_symbol[sym] = sorted(ms)

    # 実績がない銘柄のフォールバック（月パターン）
    # - 日本株: 6月/12月
    # - 海外（四半期想定）: 3/6/9/12
    for sym in set(list(last_per_share.keys()) + list(qty_by_symbol.keys())):
        if sym not in pay_months_by_symbol or not pay_months_by_symbol[sym]:
            pay_months_by_symbol[sym] = [6, 12] if _is_jp_symbol(sym) else [3, 6, 9, 12]

    # 4) 集計月の決定（mode: "pay" or "record"/"ex"）
    months = [f"{year}-{m:02d}" for m in range(1, 13)]
    monthly = {m: 0.0 for m in months}

    for sym, pay_months in pay_months_by_symbol.items():
        ps = last_per_share.get(sym)
        qty = qty_by_symbol.get(sym, 0)
        if ps is None or qty <= 0:
            continue

        per_event = float(ps) * float(qty)

        if mode in ("record", "ex"):
            # 市場ごとに支払月 → 権利確定月へシフト
            delta = -3 if _is_jp_symbol(sym) else -1
            target_months = _shift_months(pay_months, delta)
        else:
            target_months = pay_months

        for m in target_months:
            key = f"{year}-{int(m):02d}"
            if key in monthly:
                monthly[key] += per_event

    out = [{"yyyymm": k, "net": round(v, 2)} for k, v in monthly.items()]
    sum12 = round(sum(monthly.values()), 2)
    return {"months": out, "sum12": sum12}


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
            "id",
            "date",
            "ticker",
            "name",
            "broker",
            "account",
            "quantity",
            "purchase_price",
            "gross_amount",
            "tax",
            "net_amount",
            "memo",
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
    if year_q:
        filename_bits.append(str(year_q))
    if month_q:
        filename_bits.append(f"{int(month_q):02d}")
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
    # 切替: mode=pay / record（既定は pay）
    mode = (request.GET.get("mode") or "pay").lower()
    if mode not in ("pay", "record"):
        mode = "pay"

    payload = _build_forecast_payload(request.user, y, mode=mode)
    ctx = {
        "flt": _flt(request),
        "year_options": list(range(timezone.now().year + 1, timezone.now().year - 7, -1)),
        "months": payload["months"],
        "sum12": payload["sum12"],
        "payload_json": payload,
        "year": y,
        "mode": mode,
    }
    return render(request, "dividends/forecast.html", ctx)


@login_required
def dividends_forecast_json(request):
    """
    GET /dividends/forecast.json?year=YYYY&basis=pay|ex&stack=none|broker|account

    basis:
      - "pay" … 支払い月で集計
      - "ex"  … 権利確定月で集計（JP: -3ヶ月 / 海外: -1ヶ月へシフト）
    stack:
      - "none"   … 合計のみ
      - "broker" … 証券会社別
      - "account"… 口座区分別
    """
    year = _parse_year(request)

    # 後方互換（古いクエリで mode=pay|record が来ても受ける）
    basis = (request.GET.get("basis") or request.GET.get("mode") or "pay").strip().lower()
    if basis not in ("pay", "ex", "record"):
        basis = "pay"
    # mode=record を ex に寄せる
    if basis == "record":
        basis = "ex"

    stack = (request.GET.get("stack") or "none").strip().lower()
    if stack not in ("none", "broker", "account"):
        stack = "none"

    payload = _build_forecast_payload(
        user=request.user,
        year=year,
        basis=basis,   # "pay" or "ex"
        stack=stack,   # "none" | "broker" | "account"
    )
    return JsonResponse(payload)