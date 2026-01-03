# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date, datetime
from typing import Tuple
import re
from calendar import monthrange  # ★ 月末日計算に使用

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from ..models import Dividend, RealizedTrade, Holding
from ..models_cash import BrokerAccount, CashLedger, MarginState  # ★ MarginState を明示的にimport
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
    lines = ["⚠️ 余力が少なくなっています！"]
    for br, avail, cash, pct in lows:
        lines.append(f"・{br}：余力 {pct:.1f}%（残り {_format_int(avail)} 円）")
    lines.append("入金やポジション整理を検討してください。")
    return "\n".join(lines)

def _month_end_from_str(ym: str) -> date:
    """'YYYY-MM' → その月の最終日(date)"""
    ym = (ym or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})$", ym)
    if not m:
        raise ValueError("対象月の形式は YYYY-MM で入力してください。")
    y, mo = int(m.group(1)), int(m.group(2))
    last = monthrange(y, mo)[1]
    return date(y, mo, last)

# ================== dashboard ==================
@require_http_methods(["GET", "POST"])
def cash_dashboard(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        op = (request.POST.get("op") or "").strip()
        memo = (request.POST.get("memo") or "").strip()

        if op in ("deposit", "withdraw", "restrict", "tax"):
            broker = (request.POST.get("broker") or "").strip()
            if not broker:
                messages.error(request, "証券会社を選択してください。")
                return redirect("cash_dashboard")

            acc = _get_account(broker)
            if not acc:
                messages.error(request, f"{broker} の口座が見つかりません。")
                return redirect("cash_dashboard")

            # 金額取得（restrict は 0 も許容）
            try:
                amount_str = (request.POST.get("amount") or "").replace(",", "").strip()
                amount = int(amount_str)

                if op in ("deposit", "withdraw") and amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")

                # ★ tax は「マイナスもOK」ただし 0 はNG
                if op == "tax" and amount == 0:
                    raise ValueError("税金は 0 以外で入力してください。（マイナス可）")

                if op == "restrict" and amount < 0:
                    raise ValueError("拘束金は0以上で入力してください。")
            except ValueError as e:
                messages.error(request, f"金額エラー：{e}")
                return redirect("cash_dashboard")

            try:
                if op == "deposit":
                    svc.deposit(acc, amount, memo or "入金")
                    messages.success(request, f"{broker} に {amount:,} 円を入金しました。")

                elif op == "withdraw":
                    svc.withdraw(acc, amount, memo or "出金")
                    messages.success(request, f"{broker} から {amount:,} 円を出金しました。")

                elif op == "restrict":
                    # === 拘束金：当日の値を常に“上書き” ===
                    today = date.today()
                    ms, _ = MarginState.objects.get_or_create(account=acc, as_of=today)
                    ms.restricted_amount = amount
                    ms.save(update_fields=["restricted_amount"])
                    messages.success(request, f"{broker} の拘束金を {amount:,} 円に設定しました。")

                else:
                    # === 税金（源泉徴収）：対象月の月末日に記帳（±対応） ===
                    #  amount > 0 : 出金（支払い）
                    #  amount < 0 : 入金（還付/調整）
                    ym = (request.POST.get("month") or "").strip()
                    at_day = _month_end_from_str(ym)  # 例：2025-10 → 2025-10-31

                    # 同月・同社の既存税金行を消してから1件で上書き（重複防止）
                    CashLedger.objects.filter(
                        account=acc,
                        memo__startswith="税金 ",
                        at__year=at_day.year,
                        at__month=at_day.month,
                        kind__in=[CashLedger.Kind.WITHDRAW, CashLedger.Kind.DEPOSIT],
                    ).delete()

                    if amount > 0:
                        # 支払い（出金）
                        CashLedger.objects.create(
                            account=acc,
                            amount=-amount,  # 出金はマイナス
                            kind=CashLedger.Kind.WITHDRAW,
                            memo=memo or f"税金 {ym}",
                            at=at_day,
                        )
                        messages.success(request, f"{broker} の税金 {amount:,} 円（{ym}）を記録しました。")
                    else:
                        # 還付/調整（入金）
                        CashLedger.objects.create(
                            account=acc,
                            amount=abs(amount),  # 入金はプラスで保存
                            kind=CashLedger.Kind.DEPOSIT,
                            memo=memo or f"税金 {ym}",
                            at=at_day,
                        )
                        messages.success(request, f"{broker} の税金（還付） {abs(amount):,} 円（{ym}）を記録しました。")

            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")

            return redirect("cash_dashboard")

        if op == "transfer":
            messages.error(request, "振替は現在サポートしていません。")
            return redirect("cash_dashboard")

        messages.error(request, "不正な操作が指定されました。")
        return redirect("cash_dashboard")

    # ====== GET ======
    svc.ensure_default_accounts()

    # 同期（失敗しても画面は表示）
    try:
        info = up.sync_all()
        d_c = int(info.get("dividends_created", 0))
        d_u = int(info.get("dividends_updated", 0))
        r_c = int(info.get("realized_created", 0))
        r_u = int(info.get("realized_updated", 0))
        h_c = int(info.get("holdings_created", 0))
        h_u = int(info.get("holdings_updated", 0))
        if any([d_c, d_u, r_c, r_u, h_c, h_u]) or request.GET.get("force_toast") == "1":
            messages.info(
                request,
                "同期完了\n"
                f"・配当：新規 {d_c} / 更新 {d_u}\n"
                f"・実損：新規 {r_c} / 更新 {r_u}\n"
                f"・保有：新規 {h_c} / 更新 {h_u}"
            )
    except Exception as e:
        messages.error(request, f"同期に失敗：{e}")

    today = date.today()
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
        invested = int(row.get("invested_cost", 0))

        pct = (avail / cash * 100.0) if cash > 0 else None
        severity = _severity_for(row, LOW_RATIO)

        if avail < 0:
            neg_for_toast.append((broker, avail))
        elif cash > 0 and (avail / cash) < LOW_RATIO:
            lows_for_toast.append((broker, avail, cash, (avail / cash) * 100.0))

        enhanced.append({
            "broker": broker,
            "cash": cash,
            "available": avail,
            "restricted": restr,
            "invested_cost": invested,
            "month_net": month_net,
            "pct_available": pct,
            "severity": severity,
        })

    if neg_for_toast:
        messages.error(request, _make_negative_toast(neg_for_toast))
    if lows_for_toast:
        messages.warning(request, _make_low_toast(lows_for_toast))

    kpi_total, _ = svc.total_summary(today)

    return render(
        request,
        "cash/dashboard.html",
        {"brokers": enhanced, "kpi_total": kpi_total},
    )


# ================== 現金履歴台帳 ==================
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
            qs = qs.filter(kind=CashLedger.Kind.SYSTEM)  # ★ Kind.SYSTEM を使えるように

    if start:
        qs = qs.filter(at__gte=start)
    if end:
        qs = qs.filter(at__lte=end)
    if q:
        qs = qs.filter(Q(memo__icontains=q))

    # 旧式の二重表示を抑止
    qs = qs.exclude(
        Q(source_type__isnull=True) &
        (Q(memo__startswith="配当") | Q(memo__startswith="実現損益"))
    )

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

# ---- 判定 & ラベル生成（配当/実損/保有） --------------------------
def _source_is_dividend(v) -> bool:
    if v is None:
        return False
    try:
        return int(v) == int(CashLedger.SourceType.DIVIDEND)
    except Exception:
        return str(v).upper() in {"DIVIDEND", "DIV", "2"}

def _source_is_realized(v) -> bool:
    if v is None:
        return False
    try:
        return int(v) == int(CashLedger.SourceType.REALIZED)
    except Exception:
        return str(v).upper() in {"REALIZED", "REAL", "1"}

def _source_is_holding(v, memo: str | None) -> bool:
    key = (memo or "").strip()
    if v is None:
        return key.startswith("保有") or key.startswith("現物")
    s = str(v).upper()
    if s in {"HOLD", "HOLDING", "HLD"}:
        return True
    try:
        return int(v) == 3
    except Exception:
        return key.startswith("保有") or key.startswith("現物")

def _safe_str(val) -> str:
    return (val or "").strip()

def _extract_ticker_from_text(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"([0-9A-Za-z]{3,})", text)
    return m.group(1) if m else None

def _attach_source_labels(page):
    items = list(page.object_list or [])
    if not items:
        return

    div_ids, real_ids, hold_ids = set(), set(), set()
    for r in items:
        st = getattr(r, "source_type", None)
        sid = getattr(r, "source_id", None)
        mm  = getattr(r, "memo", "") or ""
        if sid is not None:
            try:
                sid_int = int(sid)
            except Exception:
                sid_int = None
        else:
            sid_int = None

        if _source_is_dividend(st):
            if sid_int is not None:
                div_ids.add(sid_int)
        elif _source_is_realized(st):
            if sid_int is not None:
                real_ids.add(sid_int)
        elif _source_is_holding(st, mm):
            if sid_int is not None:
                hold_ids.add(sid_int)

    div_map  = {d.id: d for d in Dividend.objects.filter(id__in=div_ids)}
    real_map = {x.id: x for x in RealizedTrade.objects.filter(id__in=real_ids)}
    hold_map = {h.id: h for h in Holding.objects.filter(id__in=hold_ids)}

    def build_label_from_div(d: Dividend) -> str:
        tkr = _safe_str(getattr(d, "display_ticker", None) or getattr(d, "ticker", None)).upper()
        name = _safe_str(getattr(d, "display_name", None) or getattr(d, "name", None))
        return (f"{tkr} {name}".strip() or "—")

    def build_label_from_real(x: RealizedTrade) -> str:
        tkr = _safe_str(getattr(x, "ticker", None)).upper()
        name = _safe_str(getattr(x, "name", None))
        return (f"{tkr} {name}".strip() or "—")

    def build_label_from_hold(h: Holding) -> str:
        tkr = _safe_str(getattr(h, "ticker", None)).upper()
        name = _safe_str(getattr(h, "name", None))
        return (f"{tkr} {name}".strip() or "—")

    for r in items:
        r.src_badge = None
        st  = getattr(r, "source_type", None)
        sid = getattr(r, "source_id", None)
        mm  = getattr(r, "memo", "") or ""
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

        if _source_is_holding(st, mm):
            if sid_int is not None and sid_int in hold_map:
                label = build_label_from_hold(hold_map[sid_int])
            else:
                tkr = _extract_ticker_from_text(mm)
                label = (tkr or "保有")
            r.src_badge = {"kind": "保有", "class": "chip chip-sky", "label": label}
            continue

    page.object_list = items

def _clean_params_for_pager(request: HttpRequest) -> dict:
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
    現金台帳：毎回同期 → 絞り込み → ページネーション
    """
    try:
        info = up.sync_all()
        d_c = int(info.get("dividends_created", 0))
        d_u = int(info.get("dividends_updated", 0))
        r_c = int(info.get("realized_created", 0))
        r_u = int(info.get("realized_updated", 0))
        h_c = int(info.get("holdings_created", 0))
        h_u = int(info.get("holdings_updated", 0))
        if any([d_c, d_u, r_c, r_u, h_c, h_u]) or request.GET.get("force_toast") == "1":
            messages.info(
                request,
                "同期完了\n"
                f"・配当：新規 {d_c} / 更新 {d_u}\n"
                f"・実損：新規 {r_c} / 更新 {r_u}\n"
                f"・保有：新規 {h_c} / 更新 {h_u}"
            )
    except Exception as e:
        messages.error(request, f"同期に失敗：{e}")

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
        {"page": p, "summary": summary, "params": _clean_params_for_pager(request)},
    )