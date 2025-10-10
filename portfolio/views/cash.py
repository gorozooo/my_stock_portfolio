# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date, datetime
from typing import Tuple

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from ..models import Dividend, RealizedTrade
from ..models_cash import BrokerAccount, CashLedger
from ..services import cash_service as svc
from ..services import cash_updater as up


# ================== helpers ==================
def _get_account(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    svc.ensure_default_accounts(currency=currency)
    return (
        BrokerAccount.objects.filter(broker=broker, currency=currency)
        .order_by("account_type")
        .first()
    )


def _severity_for(b: dict, low_ratio: float = 0.30) -> str:
    """
    表示用の危険度:
      - 'danger' : 余力 < 0
      - 'warn'   : 余力/預り金 < low_ratio (預り金>0 の時のみ)
      - 'ok'     : それ以外
    """
    avail = int(b.get("available", 0))
    cash  = int(b.get("cash", 0))
    if avail < 0:
        return "danger"
    if cash > 0 and (avail / cash) < low_ratio:
        return "warn"
    return "ok"


def _format_int(n: int) -> str:
    return f"{n:,}"


def _make_negative_toast(negatives: list[tuple[str, int]]) -> str:
    lines = ["⚠️ 余力がマイナスの証券口座があります！"]
    for br, val in negatives:
        lines.append(f"・{br}：{_format_int(val)} 円")
    lines.append("入出金や拘束、保有残高を確認してください。")
    return "\n".join(lines)


def _make_low_toast(lows: list[tuple[str, int, int, float]]) -> str:
    # (broker, avail, cash, pct)
    lines = ["⚠️ 余力が少なくなっています！"]
    for br, avail, cash, pct in lows:
        lines.append(f"・{br}：余力 {pct:.1f}%（残り {_format_int(avail)} 円）")
    lines.append("入金やポジション整理を検討してください。")
    return "\n".join(lines)


# ================== dashboard ==================
@require_http_methods(["GET", "POST"])
def cash_dashboard(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        op = (request.POST.get("op") or "").strip()
        memo = (request.POST.get("memo") or "").strip()

        # 入金 / 出金
        if op in ("deposit", "withdraw"):
            broker = (request.POST.get("broker") or "").strip()
            if not broker:
                messages.error(request, "証券会社を選択してください。")
                return redirect("cash_dashboard")

            acc = _get_account(broker)
            if not acc:
                messages.error(request, f"{broker} の口座が見つかりません。")
                return redirect("cash_dashboard")

            try:
                amount_str = (request.POST.get("amount") or "").replace(",", "").strip()
                amount = int(amount_str)
                if amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")

                if op == "deposit":
                    svc.deposit(acc, amount, memo or "入金")
                    messages.success(request, f"{broker} に {amount:,} 円を入金しました。")
                else:
                    svc.withdraw(acc, amount, memo or "出金")
                    messages.success(request, f"{broker} から {amount:,} 円を出金しました。")
            except ValueError as e:
                messages.error(request, f"金額エラー：{e}")
            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")
            return redirect("cash_dashboard")

        # transfer は UI から消しているが、不正 POST へのガード
        if op == "transfer":
            messages.error(request, "振替は現在サポートしていません。")
            return redirect("cash_dashboard")

        messages.error(request, "不正な操作が指定されました。")
        return redirect("cash_dashboard")

    # ====== GET ======
    svc.ensure_default_accounts()
    today = date.today()

    # 同期（失敗しても画面は表示）
    try:
        info = up.sync_all()
        d = int(info.get("dividends_created", 0))
        r = int(info.get("realized_created", 0))
        if d or r or request.GET.get("force_toast") == "1":
            messages.info(request, f"同期完了\n・配当：新規 {d} 件\n・実損：新規 {r} 件")
    except Exception as e:
        messages.error(request, f"同期に失敗：{e}")

    # === 集計 ===
    base_list = svc.broker_summaries(today)

    LOW_RATIO = 0.30
    enhanced = []
    lows_for_toast: list[tuple[str, int, int, float]] = []
    neg_for_toast:  list[tuple[str, int]] = []

    for row in base_list:
        broker = row.get("broker", "")
        cash   = int(row.get("cash", 0))
        avail  = int(row.get("available", 0))
        restr  = int(row.get("restricted", 0))
        month_net = int(row.get("month_net", 0))

        pct = (avail / cash * 100.0) if cash > 0 else None
        severity = _severity_for(row, LOW_RATIO)

        # トースト用の収集
        if avail < 0:
            neg_for_toast.append((broker, avail))
        elif cash > 0 and (avail / cash) < LOW_RATIO:
            lows_for_toast.append((broker, avail, cash, (avail / cash) * 100.0))

        enhanced.append({
            "broker": broker,
            "cash": cash,
            "available": avail,
            "restricted": restr,
            "month_net": month_net,
            "pct_available": pct,   # float | None
            "severity": severity,   # 'danger' | 'warn' | 'ok'
        })

    # 警告トースト（両方表示）
    if neg_for_toast:
        messages.error(request, _make_negative_toast(neg_for_toast))
    if lows_for_toast:
        messages.warning(request, _make_low_toast(lows_for_toast))

    # KPI 合計は既存の total_summary をそのまま使用
    kpi_total, _ = svc.total_summary(today)

    return render(
        request,
        "cash/dashboard.html",
        {
            "brokers": enhanced,      # テンプレはこの“完成形”だけを使う
            "kpi_total": kpi_total,
        },
    )


# ================== 現金履歴台帳（一覧＋フィルタ） ==================
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
    """
    page.object_list に r.src_badge を付与。
    ★ 二重表示対策：
      - source が付いた行（配当/実損）は r.memo を空にしてテンプレ側での重複表示を抑止。
    """
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
            # ← 二重表示回避：chip を出す代わりに memo は空へ
            try:
                r.memo = ""
            except Exception:
                pass
            continue

        if sid_int is not None and _source_is_realized(st):
            label = build_label_from_real(real_map[sid_int]) if sid_int in real_map else f"REAL:{sid_int}"
            r.src_badge = {"kind": "実損", "class": "chip chip-emerald", "label": label}
            # ← 二重表示回避：chip を出す代わりに memo は空へ
            try:
                r.memo = ""
            except Exception:
                pass
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
    表示前に、発生日（配当=受取日 / 実損=売買日）で Ledger.at を自動補正。
    """
    try:
        # 実装が未提供でも画面は出す（except で握りつぶす）
        svc.normalize_ledger_dates(max_rows=2000)
    except Exception:
        pass

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