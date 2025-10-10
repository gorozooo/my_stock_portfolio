# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import datetime
from typing import Tuple

from django.core.paginator import Paginator
from django.db.models import Q, Sum, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from ..models import Dividend, RealizedTrade
from ..models_cash import CashLedger
# ↑ 既にある import と重複しても構いません（Python は同一 import を許容します）

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
    """
    クエリ:
      broker=楽天|松井|SBI|ALL
      kind=ALL|DEPOSIT|WITHDRAW|XFER|SYSTEM
      start=YYYY-MM-DD / end=YYYY-MM-DD
      q=メモ部分一致
    """
    broker = (request.GET.get("broker") or "ALL").strip()
    kind   = (request.GET.get("kind") or "ALL").upper().strip()
    start  = _parse_date(request.GET.get("start"))
    end    = _parse_date(request.GET.get("end"))
    q      = (request.GET.get("q") or "").strip()

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
        wd=Sum("amount", filter=Q(kind=CashLedger.Kind.WITHDRAW)),
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

def _source_is_dividend(v) -> bool:
    if v is None:
        return False
    try:
        return int(v) == int(CashLedger.SourceType.DIVIDEND)
    except Exception:
        return str(v).upper() in {"DIVIDEND", "DIV", "2"}  # 2 は保険

def _source_is_realized(v) -> bool:
    if v is None:
        return False
    try:
        return int(v) == int(CashLedger.SourceType.REALIZED)
    except Exception:
        return str(v).upper() in {"REALIZED", "REAL", "1"}  # 1 は保険

def _safe_str(val) -> str:
    return (val or "").strip()

def _attach_source_labels(page):
    """page.object_list に r.src_badge を付与（取得不可は DIV:ID / REAL:ID でフォールバック）"""
    items = list(page.object_list or [])
    if not items:
        return

    div_ids, real_ids = set(), set()
    for r in items:
        st = getattr(r, "source_type", None)
        sid = getattr(r, "source_id", None)
        if sid is None:
            continue
        try:
            sid_int = int(sid)
        except Exception:
            continue
        if _source_is_dividend(st):
            div_ids.add(sid_int)
        elif _source_is_realized(st):
            real_ids.add(sid_int)

    div_map = {d.id: d for d in Dividend.objects.filter(id__in=div_ids)}
    real_map = {x.id: x for x in RealizedTrade.objects.filter(id__in=real_ids)}

    def build_label_from_div(d: Dividend) -> str:
        tkr = _safe_str(getattr(d, "display_ticker", None) or getattr(d, "ticker", None)).upper()
        name = _safe_str(getattr(d, "display_name", None) or getattr(d, "name", None))
        return (f"{tkr} {name}".strip() or "—")

    def build_label_from_real(x: RealizedTrade) -> str:
        tkr = _safe_str(getattr(x, "ticker", None)).upper()
        name = _safe_str(getattr(x, "name", None))
        return (f"{tkr} {name}".strip() or "—")

    for r in items:
        r.src_badge = None
        st = getattr(r, "source_type", None)
        sid = getattr(r, "source_id", None)
        try:
            sid_int = int(sid) if sid is not None else None
        except Exception:
            sid_int = None

        if sid_int is not None and _source_is_dividend(st):
            label = build_label_from_div(div_map[sid_int]) if sid_int in div_map else f"DIV:{sid_int}"
            r.src_badge = {"kind": "配当", "class": "chip chip-sky", "label": label}
            continue

        if sid_int is not None and _source_is_realized(st):
            label = build_label_from_real(real_map[sid_int]) if sid_int in real_map else f"REAL:{sid_int}"
            r.src_badge = {"kind": "実損", "class": "chip chip-emerald", "label": label}
            continue

    page.object_list = items

def _clean_params_for_pager(request: HttpRequest) -> dict:
    """page を除外し、空値も落として urlencode 用に渡す"""
    params = {}
    for k, v in request.GET.items():
        if k == "page":
            continue
        if v is None or v == "":
            continue
        params[k] = v
    return params

@require_http_methods(["GET"])
def cash_history(request: HttpRequest) -> HttpResponse:
    """
    現金台帳：通常のページネーションのみ（HTMX/カーソルなし、二重表示なし）
    """
    qs, summary = _filtered_ledger(request)

    try:
        page_no = int(request.GET.get("page") or 1)
    except Exception:
        page_no = 1

    p = Paginator(qs, PAGE_SIZE).get_page(page_no)
    _attach_source_labels(p)

    return render(
        request,
        "cash/history.html",
        {
            "page": p,
            "summary": summary,
            "params": _clean_params_for_pager(request),
        },
    )