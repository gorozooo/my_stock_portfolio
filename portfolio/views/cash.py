# [FILE] cash.py
# [PATH] portfolio/views/cash.py
#
# このファイルは何？
# - 現金ダッシュボード / 現金履歴の表示ビュー
#
# 今回の修正ポイント
# - 配当の色を紫に戻す
# - 現物買 / 現物売 / 信用新規 / 信用返済 / 現引 を全部別色にする
# - 現金履歴カードに、受渡額 / 実現損益 / 口座区分 を表示できるようにする
# - 現金ダッシュボードへ invested_cost を正しく渡す

# -*- coding: utf-8 -*-
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Tuple
import re

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from ..models import Dividend, RealizedTrade, Holding, TradeEvent
from ..models_cash import BrokerAccount, CashLedger, MarginState
from ..services import cash_service as svc
from ..services import cash_updater as up


def _get_account(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    svc.ensure_default_accounts(currency=currency)
    return svc.get_cash_account_for_broker(broker, currency=currency)


def _severity_for(b: dict, low_ratio: float = 0.30) -> str:
    avail = int(b.get("available", 0))
    cash = int(b.get("cash", 0))
    if avail < 0:
        return "danger"
    if cash > 0 and (avail / cash) < low_ratio:
        return "warn"
    return "ok"


def _format_int(n: int) -> str:
    return f"{n:,}"


def _format_yen(n, signed: bool = False) -> str:
    try:
        v = int(round(float(n or 0)))
    except Exception:
        return "—"
    return f"{v:+,} 円" if signed else f"{v:,} 円"


def _account_label(raw: str) -> str:
    s = (raw or "").upper()
    return {
        "SPEC": "特定",
        "NISA": "NISA",
        "MARGIN": "信用",
        "OTHER": "その他",
    }.get(s, raw or "—")


def _make_negative_toast(negatives: list[tuple[str, int]]) -> str:
    lines = ["⚠️ 余力がマイナスの証券口座があります！"]
    for br, val in negatives:
        lines.append(f"・{br}：{_format_int(val)} 円")
    lines.append("入出金や拘束を確認してください。")
    return "\n".join(lines)


def _make_low_toast(lows: list[tuple[str, int, int, float]]) -> str:
    lines = ["⚠️ 余力が少なくなっています！"]
    for br, avail, cash, pct in lows:
        lines.append(f"・{br}：余力 {pct:.1f}%（残り {_format_int(avail)} 円）")
    lines.append("入金やポジション整理を検討してください。")
    return "\n".join(lines)


def _month_end_from_str(ym: str) -> date:
    ym = (ym or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})$", ym)
    if not m:
        raise ValueError("対象月の形式は YYYY-MM で入力してください。")
    y, mo = int(m.group(1)), int(m.group(2))
    last = monthrange(y, mo)[1]
    return date(y, mo, last)


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

            try:
                amount_str = (request.POST.get("amount") or "").replace(",", "").strip()
                amount = int(amount_str)

                if op in ("deposit", "withdraw") and amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")

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
                    today = date.today()
                    ms, _ = MarginState.objects.get_or_create(account=acc, as_of=today)
                    ms.restricted_amount = amount
                    ms.save(update_fields=["restricted_amount"])
                    messages.success(request, f"{broker} の拘束金を {amount:,} 円に設定しました。")

                else:
                    ym = (request.POST.get("month") or "").strip()
                    at_day = _month_end_from_str(ym)

                    CashLedger.objects.filter(
                        account=acc,
                        memo__startswith="税金 ",
                        at__year=at_day.year,
                        at__month=at_day.month,
                        kind__in=[CashLedger.Kind.WITHDRAW, CashLedger.Kind.DEPOSIT],
                    ).delete()

                    if amount > 0:
                        CashLedger.objects.create(
                            account=acc,
                            amount=-amount,
                            kind=CashLedger.Kind.WITHDRAW,
                            memo=memo or f"税金 {ym}",
                            at=at_day,
                        )
                        messages.success(request, f"{broker} の税金 {amount:,} 円（{ym}）を記録しました。")
                    else:
                        CashLedger.objects.create(
                            account=acc,
                            amount=abs(amount),
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

    svc.ensure_default_accounts()

    try:
        info = up.sync_all()
        d_c = int(info.get("dividends_created", 0))
        d_u = int(info.get("dividends_updated", 0))
        t_c = int(info.get("trade_events_created", 0))
        t_u = int(info.get("trade_events_updated", 0))
        if any([d_c, d_u, t_c, t_u]) or request.GET.get("force_toast") == "1":
            messages.info(
                request,
                "同期完了\n"
                f"・配当：新規 {d_c} / 更新 {d_u}\n"
                f"・売買：新規 {t_c} / 更新 {t_u}"
            )
    except Exception as e:
        messages.error(request, f"同期に失敗：{e}")

    today = date.today()
    base_list = svc.broker_summaries(today)

    LOW_RATIO = 0.30
    enhanced = []
    lows_for_toast: list[tuple[str, int, int, float]] = []
    neg_for_toast: list[tuple[str, int]] = []

    for row in base_list:
        broker = row.get("broker", "")
        cash = int(row.get("cash", 0))
        avail = int(row.get("available", 0))
        restr = int(row.get("restricted", 0))
        month_net = int(row.get("month_net", 0))
        invested_cost = int(row.get("invested_cost", 0))

        pct = (avail / cash * 100.0) if cash > 0 else None
        severity = _severity_for(row, LOW_RATIO)

        if avail < 0:
            neg_for_toast.append((broker, avail))
        elif cash > 0 and (avail / cash) < LOW_RATIO:
            lows_for_toast.append((broker, avail, cash, (avail / cash) * 100.0))

        enhanced.append(
            {
                "broker": broker,
                "cash": cash,
                "available": avail,
                "restricted": restr,
                "month_net": month_net,
                "invested_cost": invested_cost,
                "pct_available": pct,
                "severity": severity,
            }
        )

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
    kind = (request.GET.get("kind") or "ALL").upper().strip()
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
    return str(v or "").upper() in {"DIV", "DIVIDEND"}


def _source_is_realized(v) -> bool:
    return str(v or "").upper() in {"REAL", "REALIZED"}


def _source_is_trade(v) -> bool:
    return str(v or "").upper() in {"TRD", "TRADE", "TRADE_EVENT"}


def _source_is_holding(v, memo: str | None) -> bool:
    key = (memo or "").strip()
    s = str(v or "").upper()
    return s in {"HOLD", "HOLDING", "HLD"} or key.startswith("保有") or key.startswith("現物")


def _safe_str(val) -> str:
    return (val or "").strip()


def _extract_ticker_from_text(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"([0-9A-Za-z]{3,})", text)
    return m.group(1) if m else None


def _trade_badge_class(t: TradeEvent) -> str:
    return {
        TradeEvent.EventType.SPOT_BUY: "chip chip-blue",
        TradeEvent.EventType.SPOT_SELL: "chip chip-amber",
        TradeEvent.EventType.MARGIN_OPEN: "chip chip-rose",
        TradeEvent.EventType.MARGIN_CLOSE: "chip chip-cyan",
        TradeEvent.EventType.MARGIN_TO_SPOT: "chip chip-fuchsia",
    }.get(t.event_type, "chip chip-slate")


def _trade_kind_label(t: TradeEvent) -> str:
    return {
        TradeEvent.EventType.SPOT_BUY: "現物買",
        TradeEvent.EventType.SPOT_SELL: "現物売",
        TradeEvent.EventType.MARGIN_OPEN: "信用新規",
        TradeEvent.EventType.MARGIN_CLOSE: "信用返済",
        TradeEvent.EventType.MARGIN_TO_SPOT: "現引",
    }.get(t.event_type, "売買")


def _trade_detail_lines(t: TradeEvent) -> list[str]:
    lines: list[str] = []

    if t.event_type == TradeEvent.EventType.MARGIN_TO_SPOT:
        account_txt = "信用 → 現物"
    else:
        account_txt = _account_label(t.account)
    lines.append(f"口座区分: {account_txt}")

    cash_jpy = int(t.cash_amount_jpy or 0)
    if t.event_type in (TradeEvent.EventType.SPOT_BUY, TradeEvent.EventType.SPOT_SELL):
        lines.append(f"受渡額: {_format_yen(cash_jpy, signed=True)}")
    elif t.event_type == TradeEvent.EventType.MARGIN_CLOSE:
        lines.append(f"損益反映額: {_format_yen(cash_jpy, signed=True)}")
    elif t.event_type == TradeEvent.EventType.MARGIN_TO_SPOT:
        lines.append(f"現引額: {_format_yen(cash_jpy, signed=True)}")
    elif t.event_type == TradeEvent.EventType.MARGIN_OPEN:
        lines.append("損益反映額: —")

    if t.event_type in (TradeEvent.EventType.SPOT_SELL, TradeEvent.EventType.MARGIN_CLOSE):
        try:
            if t.realized_trade_id and t.realized_trade is not None:
                pnl_jpy = int(round(float(t.realized_trade.pnl_jpy or 0)))
            else:
                pnl_jpy = int(round(float(t.realized_pnl_jpy() or 0)))
            lines.append(f"実現損益: {_format_yen(pnl_jpy, signed=True)}")
        except Exception:
            lines.append("実現損益: —")
    else:
        lines.append("実現損益: —")

    return lines


def _attach_source_labels(page):
    items = list(page.object_list or [])
    if not items:
        return

    div_ids, real_ids, hold_ids, trade_ids = set(), set(), set(), set()
    for r in items:
        st = getattr(r, "source_type", None)
        sid = getattr(r, "source_id", None)
        mm = getattr(r, "memo", "") or ""
        try:
            sid_int = int(sid) if sid is not None else None
        except Exception:
            sid_int = None

        if sid_int is None:
            continue

        if _source_is_dividend(st):
            div_ids.add(sid_int)
        elif _source_is_realized(st):
            real_ids.add(sid_int)
        elif _source_is_trade(st):
            trade_ids.add(sid_int)
        elif _source_is_holding(st, mm):
            hold_ids.add(sid_int)

    div_map = {d.id: d for d in Dividend.objects.filter(id__in=div_ids)}
    real_map = {x.id: x for x in RealizedTrade.objects.filter(id__in=real_ids)}
    hold_map = {h.id: h for h in Holding.objects.filter(id__in=hold_ids)}
    trade_map = {
        t.id: t
        for t in TradeEvent.objects.select_related("realized_trade").filter(id__in=trade_ids)
    }

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

    def build_label_from_trade(t: TradeEvent) -> str:
        tkr = _safe_str(getattr(t, "ticker", None)).upper()
        name = _safe_str(getattr(t, "name", None))
        return (f"{tkr} {name}".strip() or "—")

    for r in items:
        r.src_badge = None
        st = getattr(r, "source_type", None)
        sid = getattr(r, "source_id", None)
        mm = getattr(r, "memo", "") or ""

        try:
            sid_int = int(sid) if sid is not None else None
        except Exception:
            sid_int = None

        if sid_int is not None and _source_is_dividend(st):
            if sid_int in div_map:
                d = div_map[sid_int]
                label = build_label_from_div(d)
                detail_lines = [
                    f"口座区分: {_account_label(d.account)}",
                    f"受取額(税引後): {_format_yen(d.net_amount(), signed=False)}",
                ]
            else:
                label = f"DIV:{sid_int}"
                detail_lines = []
            r.src_badge = {
                "kind": "配当",
                "class": "chip chip-violet",
                "label": label,
                "detail_lines": detail_lines,
            }
            continue

        if sid_int is not None and _source_is_trade(st):
            if sid_int in trade_map:
                t = trade_map[sid_int]
                label = build_label_from_trade(t)
                kind = _trade_kind_label(t)
                css = _trade_badge_class(t)
                detail_lines = _trade_detail_lines(t)
            else:
                label = f"TRD:{sid_int}"
                kind = "売買"
                css = "chip chip-slate"
                detail_lines = []

            r.src_badge = {
                "kind": kind,
                "class": css,
                "label": label,
                "detail_lines": detail_lines,
            }
            continue

        if sid_int is not None and _source_is_realized(st):
            if sid_int in real_map:
                x = real_map[sid_int]
                label = build_label_from_real(x)
                detail_lines = [
                    f"口座区分: {_account_label(x.account)}",
                    f"実現損益: {_format_yen(x.pnl_jpy, signed=True)}",
                ]
            else:
                label = f"REAL:{sid_int}"
                detail_lines = []
            r.src_badge = {
                "kind": "実損",
                "class": "chip chip-emerald",
                "label": label,
                "detail_lines": detail_lines,
            }
            continue

        if _source_is_holding(st, mm):
            if sid_int is not None and sid_int in hold_map:
                h = hold_map[sid_int]
                label = build_label_from_hold(h)
                detail_lines = [
                    f"口座区分: {_account_label(h.account)}",
                    f"保有数量: {int(h.quantity or 0):,} 株",
                ]
            else:
                tkr = _extract_ticker_from_text(mm)
                label = (tkr or "保有")
                detail_lines = []

            r.src_badge = {
                "kind": "保有",
                "class": "chip chip-slate",
                "label": label,
                "detail_lines": detail_lines,
            }
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
    try:
        info = up.sync_all()
        d_c = int(info.get("dividends_created", 0))
        d_u = int(info.get("dividends_updated", 0))
        t_c = int(info.get("trade_events_created", 0))
        t_u = int(info.get("trade_events_updated", 0))
        if any([d_c, d_u, t_c, t_u]) or request.GET.get("force_toast") == "1":
            messages.info(
                request,
                "同期完了\n"
                f"・配当：新規 {d_c} / 更新 {d_u}\n"
                f"・売買：新規 {t_c} / 更新 {t_u}"
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