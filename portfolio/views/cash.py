# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date, datetime
from typing import Tuple
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from ..models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from ..services import cash_service as svc
from ..services import cash_updater as up


# ================== dashboard ==================
def _get_account(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    svc.ensure_default_accounts(currency=currency)
    return (
        BrokerAccount.objects.filter(broker=broker, currency=currency)
        .order_by("account_type")
        .first()
    )


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

    return render(
        request,
        "cash/dashboard.html",
        {"brokers": brokers, "kpi_total": kpi_total},
    )


# ================== 共通ヘルパー ==================
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


def _filtered_ledger(request: HttpRequest) -> Tuple[QuerySet, dict]:
    broker = request.GET.get("broker", "ALL")
    kind = request.GET.get("kind", "ALL").upper()
    start = _parse_date(request.GET.get("start"))
    end = _parse_date(request.GET.get("end"))
    q = (request.GET.get("q") or "").strip()

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
    if start:
        qs = qs.filter(at__gte=start)
    if end:
        qs = qs.filter(at__lte=end)
    if q:
        qs = qs.filter(Q(memo__icontains=q))

    agg = qs.aggregate(
        total=Sum("amount"),
        dep=Sum("amount", filter=Q(kind=CashLedger.Kind.DEPOSIT)),
        wd=Sum("amount", filter=Q(kind=CashLedger.Kind.WITHDRAW)),
    )
    summary = {
        "total": int(agg["total"] or 0),
        "deposit": int(agg["dep"] or 0),
        "withdraw": int(agg["wd"] or 0),
    }
    return qs, summary


def _attach_source_labels(page):
    items = list(page.object_list or [])
    if not items:
        return
    div_ids = [r.source_id for r in items if r.source_type == CashLedger.SourceType.DIVIDEND]
    real_ids = [r.source_id for r in items if r.source_type == CashLedger.SourceType.REALIZED]
    div_map = {d.id: d for d in Dividend.objects.filter(id__in=div_ids)}
    real_map = {x.id: x for x in RealizedTrade.objects.filter(id__in=real_ids)}

    for r in items:
        if r.source_type == CashLedger.SourceType.DIVIDEND:
            d = div_map.get(r.source_id)
            if d:
                r.src_badge = {"kind": "配当", "label": f"{d.ticker} {d.name}"}
        elif r.source_type == CashLedger.SourceType.REALIZED:
            x = real_map.get(r.source_id)
            if x:
                r.src_badge = {"kind": "実損", "label": f"{x.ticker} {x.name}"}
    page.object_list = items


# ================== 一覧 / 読込 ==================
@require_http_methods(["GET"])
def cash_history(request: HttpRequest) -> HttpResponse:
    """現金台帳（ページング一発描画・重複防止版）"""
    svc.ensure_default_accounts()
    qs, summary = _filtered_ledger(request)

    # page が複数渡っても最初の1個だけ使う
    page_param = request.GET.getlist("page")
    page_number = page_param[0] if page_param else "1"

    paginator = Paginator(qs, PAGE_SIZE)
    page_obj = paginator.get_page(page_number)

    _attach_source_labels(page_obj)

    return render(
        request,
        "cash/history.html",
        {
            "page": page_obj,
            "summary": summary,
            "params": request.GET,
        },
    )


@require_http_methods(["GET"])
def cash_history_page(request: HttpRequest) -> HttpResponse:
    qs, _ = _filtered_ledger(request)
    page_no = int(request.GET.get("page") or 1)
    paginator = Paginator(qs, PAGE_SIZE)
    p = paginator.get_page(page_no)
    _attach_source_labels(p)
    return render(request, "cash/_history_list.html", {"page": p, "params": request.GET})


# ================== 編集 / 削除 ==================
@require_http_methods(["GET", "POST"])
def ledger_edit(request: HttpRequest, pk: int):
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
def ledger_delete(request: HttpRequest, pk: int):
    obj = get_object_or_404(CashLedger, pk=pk)
    try:
        obj.delete()
        messages.success(request, "台帳を削除しました。")
    except Exception as e:
        messages.error(request, f"削除に失敗：{e}")
    return redirect("cash_history")