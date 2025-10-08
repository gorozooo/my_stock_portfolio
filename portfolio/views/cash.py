from __future__ import annotations
from datetime import date, datetime
import re

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from ..models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from ..services import cash_service as svc
from ..services import cash_updater as up


# ================== dashboard（既存） ==================
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

    # 自動同期（エラーでも画面は出す）
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
        {
            "brokers": brokers,
            "kpi_total": kpi_total,
        },
    )


# ================== 履歴：共通ヘルパー ==================
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
    クエリパラメータ:
      broker=楽天|松井|SBI|ALL
      kind=ALL|DEPOSIT|WITHDRAW|XFER|SYSTEM
      start=YYYY-MM-DD
      end=YYYY-MM-DD
      q=メモ部分一致
    """
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
        elif kind == "SYSTEM":
            qs = qs.filter(kind=CashLedger.Kind.SYSTEM)

    if start:
        qs = qs.filter(at__gte=start)
    if end:
        qs = qs.filter(at__lte=end)
    if q:
        qs = qs.filter(Q(memo__icontains=q))

    # 集計（現在のフィルタに対して）
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
    """
    CashLedger.SourceType が数値/文字の両方に対応
    """
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
    """
    page.object_list に r.src_badge を付与。
    可能な限り「コード 名称」を出し、取れない場合は REAL:ID / DIV:ID を最後の保険として表示。
    """
    items = list(page.object_list or [])
    if not items:
        return

    # 対象IDを収集
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


# ============ 無限スクロール重複対策: リクエスト指紋を直前と比較 ============
def _page_fingerprint(request: HttpRequest) -> str:
    """同一パラメータの二重発火を弾くための簡易フィンガープリント"""
    p = request.GET
    # 必要十分なキーだけを使う（順序は固定）
    keys = ("broker", "kind", "start", "end", "q", "page")
    vals = [str(p.get(k, "")).strip() for k in keys]
    return "|".join(vals)


def _is_dup_request_and_mark(request: HttpRequest, key_name: str) -> bool:
    """直前のフィンガープリントと一致したら True を返す（＝重複）"""
    fp = _page_fingerprint(request)
    last = request.session.get(key_name)
    request.session[key_name] = fp
    # 直前と全く同じなら重複
    return last == fp


# ================== 履歴：一覧 / 追加読込 ==================
@require_http_methods(["GET"])
def cash_history(request: HttpRequest) -> HttpResponse:
    svc.ensure_default_accounts()
    qs, summary = _filtered_ledger(request)
    p = Paginator(qs, PAGE_SIZE).get_page(1)

    _attach_source_labels(p)

    # 初回表示の重複履歴キーをリセット
    request.session.pop("cash_history_last_partial", None)

    return render(
        request,
        "cash/history.html",
        {
            "page": p,
            "summary": summary,
            "params": request.GET,
        },
    )


@require_http_methods(["GET"])
def cash_history_page(request: HttpRequest) -> HttpResponse:
    # ★ 同一パラメータで直前にも返しているなら 204 を返して重複追加を防ぐ
    if _is_dup_request_and_mark(request, "cash_history_last_partial"):
        return HttpResponse(status=204)

    qs, _ = _filtered_ledger(request)
    page_no = int(request.GET.get("page") or 1)
    p = Paginator(qs, PAGE_SIZE).get_page(page_no)

    _attach_source_labels(p)

    return render(
        request,
        "cash/_history_list.html",
        {
            "page": p,
            "params": request.GET,
        },
    )


# ================== 履歴：編集 / 削除 ==================
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