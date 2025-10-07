from __future__ import annotations
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpRequest, HttpResponse
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from datetime import date, datetime
from django.core.paginator import Paginator
from django.db.models import Sum, Q

from ..models_cash import BrokerAccount, CashLedger
from ..services import cash_service as svc
from ..services import cash_updater as up
from ..models import Dividend, RealizedTrade


# ================== dashboard ==================
def _get_account(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    svc.ensure_default_accounts(currency=currency)
    return BrokerAccount.objects.filter(broker=broker, currency=currency).order_by("account_type").first()


@require_http_methods(["GET", "POST"])
def cash_dashboard(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        op = request.POST.get("op")
        amount = int(request.POST.get("amount") or 0)
        memo = request.POST.get("memo", "")

        if op in ("deposit", "withdraw"):
            broker = request.POST.get("broker", "")
            acc = _get_account(broker)
            if not acc:
                messages.error(request, f"{broker} の口座が見つかりません。")
                return redirect("cash_dashboard")
            try:
                if amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")
                if op == "deposit":
                    svc.deposit(acc, amount, memo or "入金")
                    messages.success(request, f"{broker} に {amount:,} 円を入金しました。")
                else:
                    svc.withdraw(acc, amount, memo or "出金")
                    messages.success(request, f"{broker} から {amount:,} 円を出金しました。")
            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")
            return redirect("cash_dashboard")

        if op == "transfer":
            src_b = request.POST.get("src_broker", "")
            dst_b = request.POST.get("dst_broker", "")
            src = _get_account(src_b)
            dst = _get_account(dst_b)
            if not src or not dst:
                messages.error(request, "振替元/先の口座が見つかりません。")
                return redirect("cash_dashboard")
            try:
                if amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")
                svc.transfer(src, dst, amount, memo or "口座間振替")
                messages.success(request, f"{src_b} → {dst_b} へ {amount:,} 円を振替えました。")
            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")
            return redirect("cash_dashboard")

    # GET
    svc.ensure_default_accounts()
    today = date.today()

    try:
        info = up.sync_all()
        d = int(info.get("dividends_created", 0))
        r = int(info.get("realized_created", 0))
        if d or r or request.GET.get("force_toast") == "1":
            messages.info(request, f"同期完了\n・配当：新規 {d} 件\n・実損：新規 {r} 件")
    except Exception as e:
        messages.error(request, f"同期に失敗：{e}")

    brokers = svc.broker_summaries(today)
    kpi_total, _ = svc.total_summary(today)

    return render(request, "cash/dashboard.html", {
        "brokers": brokers,
        "kpi_total": kpi_total,
    })


# ================== 履歴 共通 ==================
PAGE_SIZE = 30


def _parse_date(s: str | None):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def _filtered_ledger(request: HttpRequest):
    """
    クエリ:
      broker=楽天|松井|SBI|ALL
      kind=ALL|DEPOSIT|WITHDRAW|XFER|SYSTEM
      start/end=YYYY-MM-DD
      q=メモ部分一致
    """
    broker = request.GET.get("broker", "ALL")
    kind = (request.GET.get("kind", "ALL") or "ALL").upper()
    start = _parse_date(request.GET.get("start"))
    end   = _parse_date(request.GET.get("end"))
    q     = (request.GET.get("q") or "").trim() if hasattr(str, "trim") else (request.GET.get("q") or "").strip()

    qs = CashLedger.objects.select_related("account").order_by("-at", "-id")

    if broker and broker != "ALL":
        qs = qs.filter(account__broker=broker)

    if kind != "ALL":
        if kind == "DEPOSIT":
            qs = qs.filter(kind=CashLedger.Kind.DEPOSIT)
        elif kind == "WITHDRAW":
            qs = qs.filter(kind=CashLedger.Kind.WITHDRAW)
        elif kind == "XFER":
            qs = qs.filter(kind__in=[CashLedger.Kind.XFER_IN, CashLedger.Kind.XFER_OUT])
        elif kind == "SYSTEM":
            qs = qs.filter(kind=CashLedger.Kind.SYSTEM)

    if start:
        qs = qs.filter(at__gte=start)
    if end:
        qs = qs.filter(at__lte=end)
    if q:
        qs = qs.filter(Q(memo__icontains=q))

    agg = qs.aggregate(
        total=Sum("amount"),
        dep=Sum("amount", filter=Q(kind=CashLedger.Kind.DEPOSIT)),
        wd =Sum("amount", filter=Q(kind=CashLedger.Kind.WITHDRAW)),
        xin=Sum("amount", filter=Q(kind=CashLedger.Kind.XFER_IN)),
        xout=Sum("amount", filter=Q(kind=CashLedger.Kind.XFER_OUT)),
    )
    summary = {
        "total": int(agg["total"] or 0),
        "deposit": int(agg["dep"] or 0),
        "withdraw": int(agg["wd"] or 0),
        "xfer_in": int(agg["xin"] or 0),
        "xfer_out": int(agg["xout"] or 0),
    }
    return qs, summary


def _attach_source_labels(page):
    """
    右上バッジ用の銘柄ラベルを付与。
    source_type が壊れていても source_id があれば両方に当てて解決を試みる。
    付与先: r.src_badge = {"kind","label"}
    """
    items = list(page.object_list)
    if not items:
        return

    ids = {int(r.source_id) for r in items if getattr(r, "source_id", None)}
    if not ids:
        page.object_list = items
        return

    # 両方照会して存在した方を採用（N+1回避）
    div_map  = {d.id: d for d in Dividend.objects.filter(id__in=ids)}
    real_map = {x.id: x for x in RealizedTrade.objects.filter(id__in=ids)}

    for r in items:
        r.src_badge = None
        sid = getattr(r, "source_id", None)
        if not sid:
            continue
        sid = int(sid)

        d = div_map.get(sid)
        x = real_map.get(sid)

        if d is not None:
            tkr = (getattr(d, "display_ticker", None) or d.ticker or "").upper()
            name = (getattr(d, "display_name", None) or d.name or "")
            r.src_badge = {"kind": "配当", "label": (f"{tkr} {name}".strip() or "—")}
        elif x is not None:
            tkr = (x.ticker or "").upper()
            name = (x.name or "")
            r.src_badge = {"kind": "実損", "label": (f"{tkr} {name}".strip() or "—")}

    page.object_list = items


# ================== 履歴：一覧 / ページング ==================
def cash_history(request: HttpRequest) -> HttpResponse:
    svc.ensure_default_accounts()
    qs, summary = _filtered_ledger(request)
    p = Paginator(qs, PAGE_SIZE).get_page(1)
    _attach_source_labels(p)
    return render(request, "cash/history.html", {
        "page": p,
        "summary": summary,
        "params": request.GET,
    })


def cash_history_page(request: HttpRequest) -> HttpResponse:
    qs, _ = _filtered_ledger(request)
    page_no = int(request.GET.get("page") or 1)
    p = Paginator(qs, PAGE_SIZE).get_page(page_no)
    _attach_source_labels(p)
    return render(request, "cash/_history_list.html", {
        "page": p,
        "params": request.GET,
    })


# ================== 履歴：編集 / 削除 ==================
@require_http_methods(["GET", "POST"])
def cash_ledger_edit(request: HttpRequest, pk: int):
    obj = get_object_or_404(CashLedger, pk=pk)
    if request.method == "POST":
        try:
            obj.at = _parse_date(request.POST.get("at")) or obj.at
            obj.amount = int(request.POST.get("amount") or obj.amount)
            obj.memo = request.POST.get("memo", obj.memo)
            obj.kind = int(request.POST.get("kind") or obj.kind)
            obj.save()
            messages.success(request, "台帳を更新しました。")
            return redirect("cash_history")
        except Exception as e:
            messages.error(request, f"更新に失敗：{e}")
    return render(request, "cash/edit_ledger.html", {"obj": obj})


@require_http_methods(["POST"])
def cash_ledger_delete(request: HttpRequest, pk: int):
    obj = get_object_or_404(CashLedger, pk=pk)
    try:
        obj.delete()
        messages.success(request, "台帳を削除しました。")
    except Exception as e:
        messages.error(request, f"削除に失敗：{e}")
    return redirect("cash_history")