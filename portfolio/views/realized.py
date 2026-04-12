# [FILE] realized.py
# [PATH] portfolio/views/realized.py
#
# このファイルは何？
# - 実現損益の一覧表示・分析表示・月次表示・CSV出力・クローズシート表示を担当する view
#
# 今回の修正ポイント
# - 上部固定UI用に「証券会社 / 年 / 月」フィルタを追加
# - 同じ条件が 総合 / 流れ / 銘柄 / 明細 に全部連動するように統一
# - スマホ向けの新レイアウトに合わせて、AIコメント用の文言も view 側で生成

from __future__ import annotations

from decimal import Decimal
from datetime import date as _date, timedelta, datetime
from typing import Any
import csv
import logging
import traceback

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, F, Value, Case, When, IntegerField, Q
from django.db.models.functions import Coalesce, TruncMonth, TruncYear
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.utils.encoding import smart_str
from django.utils.dateparse import parse_date

from ..models import Holding, RealizedTrade
from ..services.trades import metrics_service as mts

logger = logging.getLogger(__name__)

DEC2 = mts.DEC2
DEC4 = mts.DEC4

# 互換のための別名
_parse_period = mts.parse_period
_parse_period_from_request = mts.parse_period_from_request
_with_metrics = mts.with_metrics
_aggregate = mts.aggregate
_aggregate_by_broker = mts.aggregate_by_broker


def _safe_int(v: Any) -> int | None:
    try:
        if v in (None, "", "ALL"):
            return None
        return int(str(v))
    except Exception:
        return None


def _ui_filters_from_request(request) -> dict[str, str]:
    broker = (request.GET.get("broker_filter") or "ALL").strip().upper()
    if broker not in {"ALL", "RAKUTEN", "MATSUI", "SBI"}:
        broker = "ALL"

    year_val = _safe_int(request.GET.get("year_filter"))
    month_val = _safe_int(request.GET.get("month_filter"))

    year = str(year_val) if year_val else ""
    month = f"{month_val:02d}" if month_val and 1 <= month_val <= 12 else ""

    return {
        "q": (request.GET.get("q") or "").strip(),
        "broker": broker,
        "year": year,
        "month": month,
    }


def _apply_text_filter(qs, q: str):
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
    return qs


def _apply_ui_filters(qs, ui_filters: dict[str, str]):
    broker = ui_filters.get("broker") or "ALL"
    year = ui_filters.get("year") or ""
    month = ui_filters.get("month") or ""

    if broker != "ALL":
        qs = qs.filter(broker=broker)

    if year:
        qs = qs.filter(trade_at__year=int(year))
        if month:
            qs = qs.filter(trade_at__month=int(month))

    return qs


def _apply_period_filters(qs, request, ui_filters: dict[str, str]):
    """
    年/月の固定UIが選ばれている時は、そちらを優先。
    何も選ばれていない時だけ preset/start/end を使う。
    """
    year = ui_filters.get("year") or ""
    if year:
        return qs, None, None, "UI_FILTER"

    start, end, preset = _parse_period(request)
    if start:
        qs = qs.filter(trade_at__gte=start)
    if end:
        qs = qs.filter(trade_at__lte=end)
    return qs, start, end, preset


def _available_years_for_user(user) -> list[str]:
    years = []
    try:
        for d in RealizedTrade.objects.filter(user=user).dates("trade_at", "year", order="DESC"):
            years.append(str(d.year))
    except Exception:
        years = []
    return years


def _month_choices() -> list[dict[str, str]]:
    return [{"value": f"{i:02d}", "label": f"{i}月"} for i in range(1, 13)]


def _build_ai_insight(metrics: dict[str, Any], label: str = "総合") -> dict[str, str]:
    n = int(metrics.get("n") or 0)
    pnl = float(metrics.get("pnl") or 0)
    win_rate = float(metrics.get("win_rate") or 0)
    pf = float(metrics.get("pf") or 0) if metrics.get("pf") not in (None, "") else None
    avg_hold = float(metrics.get("avg_hold_days") or 0) if metrics.get("avg_hold_days") not in (None, "") else None
    fee = float(metrics.get("fee") or 0)
    avg_pct = float(metrics.get("avg_pnl_pct") or 0) if metrics.get("avg_pnl_pct") not in (None, "") else None
    profit_sum = float(metrics.get("profit_sum") or 0)
    loss_sum = float(metrics.get("loss_sum") or 0)

    if n == 0:
        return {
            "ai_summary": f"{label}はまだ分析できるデータがありません。",
            "ai_good": "まずは数件でも実現データを貯めるのが最優先です。",
            "ai_warn": "データ不足なので、今の段階では結論を出しすぎない方が安全です。",
            "ai_next": "1ヶ月分か数件分たまったら、月別と銘柄別を見比べる流れがおすすめです。",
        }

    if pnl >= 0 and win_rate >= 60 and (pf is None or pf >= 1.2):
        ai_summary = f"{label}はかなり安定寄りです。利益と勝率の両方が崩れていません。"
    elif pnl >= 0:
        ai_summary = f"{label}は利益側です。次は“何で勝っているか”を固定化するとさらに強くなります。"
    else:
        ai_summary = f"{label}はまだ損失先行です。まずは負け方を小さくする視点が最優先です。"

    if win_rate >= 60:
        ai_good = "勝率が高めで、エントリー精度は崩れていません。勝ちパターンの再現性が強みです。"
    elif profit_sum > abs(loss_sum):
        ai_good = "勝率が特別高くなくても、勝った時の取り方で支えられています。"
    elif avg_pct is not None and avg_pct > 0:
        ai_good = "1件あたりの平均成績は悪くありません。極端に崩れた取引を減らせば改善しやすいです。"
    else:
        ai_good = "今は強みを探している段階です。まずは勝てた条件を少数でも固定化するのが近道です。"

    if avg_hold is not None and avg_hold >= 60:
        ai_warn = "平均保有日数が長めです。利確の遅れか、見切り遅れが混ざっていないか要注意です。"
    elif fee >= 100000:
        ai_warn = "手数料負担がやや重めです。回転数が多い期間はコスト確認を優先したいです。"
    elif pnl < 0 and loss_sum < 0:
        ai_warn = "大きな負けを作った月や銘柄がないか、先にそこを絞るのが効きます。"
    elif pf is not None and pf < 1:
        ai_warn = "利益より損失の方が重い状態です。勝率より先に損失の深さを見たいです。"
    else:
        ai_warn = "大崩れは見えにくいですが、勝率だけで安心せず“損失の深さ”を定期確認したいです。"

    if pnl >= 0:
        ai_next = "次は『流れ』で月別カードを見て、そのあと『銘柄』で上位と下位を確認すると改善点が見つけやすいです。"
    else:
        ai_next = "まず『流れ』で崩れた月を1つ見つけて、次に『明細』でその月の共通点を1つだけ拾うのがおすすめです。"

    return {
        "ai_summary": ai_summary,
        "ai_good": ai_good,
        "ai_warn": ai_warn,
        "ai_next": ai_next,
    }


def _decorate_agg_with_ai(agg: dict[str, Any], label: str = "総合") -> dict[str, Any]:
    if not agg:
        agg = {}
    ai = _build_ai_insight(agg, label)
    out = dict(agg)
    out.update(ai)
    return out


def _decorate_broker_aggs(agg_brokers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    broker_name_map = {
        "RAKUTEN": "楽天証券",
        "MATSUI": "松井証券",
        "SBI": "SBI証券",
    }
    for b in agg_brokers:
        label = broker_name_map.get((b.get("broker") or "").upper(), "この証券会社")
        row = dict(b)
        row.update(_build_ai_insight(b, label))
        out.append(row)
    return out


@login_required
@require_GET
def monthly_kpis_partial(request):
    q = (request.GET.get("q") or "").strip()
    start, end = _parse_period_from_request(request)
    ui_filters = _ui_filters_from_request(request)

    qs = RealizedTrade.objects.filter(user=request.user, trade_at__range=(start, end))
    qs = _apply_ui_filters(qs, ui_filters)
    qs = _apply_text_filter(qs, q)
    qs = _with_metrics(qs)

    total = 0
    win = 0
    pnl_pos = Decimal("0")
    pnl_neg = Decimal("0")
    pct_list = []
    hold_list = []

    for t in qs:
        cf_jpy = Decimal(str(getattr(t, "pnl_jpy_calc", Decimal("0")) or 0))

        if cf_jpy > 0:
            pnl_pos += cf_jpy
        elif cf_jpy < 0:
            pnl_neg += cf_jpy

        if t.side == "SELL":
            total += 1
            if cf_jpy > 0:
                win += 1

            try:
                if t.basis is not None and t.qty and Decimal(str(t.qty)) > 0:
                    denom = Decimal(str(t.basis)) * Decimal(str(t.qty))
                    if denom > 0:
                        pct_list.append((cf_jpy / denom) * Decimal("100"))
            except Exception:
                pass

        if t.hold_days is not None:
            try:
                hd = int(t.hold_days)
                if hd >= 0:
                    hold_list.append(hd)
            except Exception:
                pass

    avg_pct = (sum(pct_list) / Decimal(len(pct_list))) if pct_list else None
    winrate = (win / total * 100.0) if total > 0 else None
    pf = (float(pnl_pos) / abs(float(pnl_neg))) if pnl_neg != 0 else None
    avg_hold = (sum(hold_list) / len(hold_list)) if hold_list else None

    ctx = {
        "avg_pct": float(avg_pct) if avg_pct is not None else None,
        "winrate": float(winrate) if winrate is not None else None,
        "pf": float(pf) if pf is not None else None,
        "avg_hold": float(avg_hold) if avg_hold is not None else None,
    }
    return render(request, "realized/_month_kpis.html", ctx)


@login_required
@require_GET
def monthly_breakdown_partial(request):
    q = (request.GET.get("q") or "").strip()
    start, end = _parse_period_from_request(request)
    ui_filters = _ui_filters_from_request(request)

    qs = RealizedTrade.objects.filter(user=request.user, trade_at__range=(start, end))
    qs = _apply_ui_filters(qs, ui_filters)
    qs = _apply_text_filter(qs, q)
    qs = _with_metrics(qs)

    broker_label = dict(RealizedTrade.BROKER_CHOICES)
    acct_label = dict(RealizedTrade.ACCOUNT_CHOICES)

    brokers = (
        qs.values("broker")
        .annotate(n=Count("id"), pnl=Sum("pnl_jpy_calc"))
        .order_by("broker")
    )
    accounts = (
        qs.values("account")
        .annotate(n=Count("id"), pnl=Sum("pnl_jpy_calc"))
        .order_by("account")
    )

    brokers_view = [
        {
            "label": broker_label.get(row["broker"], row["broker"]),
            "pnl": float(row["pnl"] or 0),
            "n": row["n"],
        }
        for row in brokers
    ]
    accounts_view = [
        {
            "label": acct_label.get(row["account"], row["account"]),
            "pnl": float(row["pnl"] or 0),
            "n": row["n"],
        }
        for row in accounts
    ]

    return render(
        request,
        "realized/_month_breakdown.html",
        {
            "brokers": brokers_view,
            "accounts": accounts_view,
        },
    )


@login_required
@require_GET
def monthly_topworst_partial(request):
    q = (request.GET.get("q") or "").strip()
    ui_filters = _ui_filters_from_request(request)

    qs = RealizedTrade.objects.all()
    if any(f.name == "user" for f in RealizedTrade._meta.fields):
        qs = qs.filter(user=request.user)

    qs = _apply_ui_filters(qs, ui_filters)
    qs = _apply_text_filter(qs, q)

    preset = (request.GET.get("preset") or "").upper()
    start_raw = (request.GET.get("start") or "").strip()
    end_raw = (request.GET.get("end") or "").strip()

    start = end = None
    try:
        if start_raw:
            start = timezone.datetime.fromisoformat(start_raw).date()
        if end_raw:
            end = timezone.datetime.fromisoformat(end_raw).date()
    except Exception:
        start = end = None

    today = timezone.localdate()

    if not ui_filters.get("year"):
        if not (start and end):
            if preset == "THIS_MONTH":
                start = today.replace(day=1)
                end = today
            elif preset == "THIS_YEAR":
                start = today.replace(month=1, day=1)
                end = today
            elif preset == "LAST_12M":
                start = today - timedelta(days=365)
                end = today
            else:
                start = today - timedelta(days=365)
                end = today

        qs = qs.filter(trade_at__gte=start, trade_at__lte=end)

    qs = _with_metrics(qs)

    dec0 = Value(0, output_field=DEC2)

    monthly = (
        qs.annotate(m=TruncMonth("trade_at"))
        .values("m")
        .annotate(
            pnl=Coalesce(Sum("pnl_jpy_calc", output_field=DEC2), dec0),
        )
        .order_by("m")
    )

    items = []
    for r in monthly:
        dt = r["m"]
        label = dt.strftime("%Y-%m") if dt else ""
        items.append({"label": label, "pnl": float(r.get("pnl") or 0)})

    top = sorted(items, key=lambda x: x["pnl"], reverse=True)[:3]
    worst = sorted(items, key=lambda x: x["pnl"])[:3]

    return render(request, "realized/_monthly_topworst.html", {"top": top, "worst": worst})


@login_required
@require_GET
def chart_daily_heat_json(request, year: int, month: int):
    q = (request.GET.get("q") or "").strip()
    ui_filters = _ui_filters_from_request(request)

    try:
        start = _date(int(year), int(month), 1)
    except Exception:
        start = timezone.localdate().replace(day=1)

    if start.month == 12:
        next_first = _date(start.year + 1, 1, 1)
    else:
        next_first = _date(start.year, start.month + 1, 1)

    qs = RealizedTrade.objects.filter(
        user=request.user,
        trade_at__gte=start,
        trade_at__lt=next_first,
    )
    qs = _apply_ui_filters(qs, {"broker": ui_filters["broker"], "year": "", "month": "", "q": q})
    qs = _apply_text_filter(qs, q)
    qs = _with_metrics(qs)

    daily = (
        qs.values("trade_at")
        .annotate(
            pnl=Coalesce(
                Sum("pnl_jpy_calc", output_field=DEC2),
                Value(Decimal("0"), output_field=DEC2),
            ),
            cash_spec=Coalesce(
                Sum(
                    Case(
                        When(
                            account__in=["SPEC", "NISA"],
                            then=F("cashflow_calc_jpy"),
                        ),
                        default=Value(Decimal("0"), output_field=DEC2),
                        output_field=DEC2,
                    )
                ),
                Value(Decimal("0"), output_field=DEC2),
            ),
            cash_margin=Coalesce(
                Sum(
                    Case(
                        When(account="MARGIN", then=F("pnl_jpy_calc")),
                        default=Value(Decimal("0"), output_field=DEC2),
                        output_field=DEC2,
                    )
                ),
                Value(Decimal("0"), output_field=DEC2),
            ),
        )
        .order_by("trade_at")
    )

    labels, pnl, cash_spec, cash_margin = [], [], [], []
    vmin = vmax = None
    for r in daily:
        d = r["trade_at"]
        label = d.strftime("%Y-%m-%d") if d else ""
        labels.append(label)

        p = r["pnl"] or Decimal("0")
        cs = r["cash_spec"] or Decimal("0")
        cm = r["cash_margin"] or Decimal("0")

        pf = float(p)
        pnl.append(pf)
        cash_spec.append(float(cs))
        cash_margin.append(float(cm))

        vmin = pf if vmin is None else min(vmin, pf)
        vmax = pf if vmax is None else max(vmax, pf)

    return JsonResponse(
        {
            "year": start.year,
            "month": start.month,
            "labels": labels,
            "pnl": pnl,
            "cash_spec": cash_spec,
            "cash_margin": cash_margin,
            "min": vmin if vmin is not None else 0.0,
            "max": vmax if vmax is not None else 0.0,
        }
    )


@login_required
@require_GET
def monthly_page(request):
    q = (request.GET.get("q") or "").strip()
    ctx = {
        "q": q,
        "default_preset": "LAST_12M",
        "default_freq": "month",
    }
    return render(request, "realized/monthly.html", ctx)


@login_required
@require_GET
def summary_period_partial(request):
    q = (request.GET.get("q") or "").strip()
    freq = (request.GET.get("freq") or "month").lower()
    focus = (request.GET.get("focus") or "").strip()

    ui_filters = _ui_filters_from_request(request)

    qs = RealizedTrade.objects.filter(user=request.user)
    qs = _apply_text_filter(qs, q)
    qs = _apply_ui_filters(qs, ui_filters)

    if ui_filters.get("year"):
        start = end = None
        preset = "UI_FILTER"
    else:
        qs, start, end, preset = _apply_period_filters(qs, request, ui_filters)

    qs = _with_metrics(qs)

    if freq == "year":
        bucket = TruncYear("trade_at")
        label_format = "%Y"
    else:
        bucket = TruncMonth("trade_at")
        label_format = "%Y-%m"

    grouped = (
        qs.annotate(period=bucket)
        .values("period")
        .annotate(
            n=Coalesce(Count("id"), Value(0), output_field=IntegerField()),
            qty=Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),
            fee=Coalesce(
                Sum(
                    Coalesce(F("fee"), Value(Decimal("0"), output_field=DEC2))
                ),
                Value(Decimal("0"), output_field=DEC2),
            ),
            cash_spec=Coalesce(
                Sum(
                    "cashflow_calc_jpy",
                    filter=Q(account__in=["SPEC", "NISA"]),
                    output_field=DEC2,
                ),
                Value(Decimal("0"), output_field=DEC2),
            ),
            cash_margin=Coalesce(
                Sum(
                    "pnl_jpy_calc",
                    filter=Q(account="MARGIN"),
                    output_field=DEC2,
                ),
                Value(Decimal("0"), output_field=DEC2),
            ),
            pnl=Coalesce(
                Sum("pnl_jpy_calc", output_field=DEC2),
                Value(Decimal("0"), output_field=DEC2),
            ),
        )
        .order_by("period")
    )

    rows = []
    selected = None
    for r in grouped:
        label = r["period"].strftime(label_format) if r["period"] else ""
        cash_total = (r["cash_spec"] or Decimal("0")) + (r["cash_margin"] or Decimal("0"))
        row = {
            "period": r["period"],
            "label": label,
            "n": r["n"],
            "qty": r["qty"],
            "fee": r["fee"],
            "cash_spec": r["cash_spec"],
            "cash_margin": r["cash_margin"],
            "cash_total": cash_total,
            "pnl": r["pnl"],
        }
        rows.append(row)
        if focus and label == focus:
            selected = row

    ctx = {
        "rows": rows,
        "preset": preset,
        "freq": freq,
        "start": start,
        "end": end,
        "q": q,
        "focus": focus if selected else "",
        "selected": selected,
        "ui_filters": ui_filters,
    }
    return render(request, "realized/_summary_period.html", ctx)


@login_required
def realized_summary_partial(request):
    ui_filters = _ui_filters_from_request(request)
    q = ui_filters["q"]

    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    qs = _apply_text_filter(qs, q)
    qs = _apply_ui_filters(qs, ui_filters)

    agg = _decorate_agg_with_ai(_aggregate(qs), "総合")
    agg_brokers = _decorate_broker_aggs(_aggregate_by_broker(qs))

    return render(
        request,
        "realized/_summary.html",
        {"agg": agg, "agg_brokers": agg_brokers, "q": q, "ui_filters": ui_filters},
    )


@login_required
@require_GET
def chart_monthly_json(request):
    ui_filters = _ui_filters_from_request(request)
    q = ui_filters["q"]

    qs = RealizedTrade.objects.filter(user=request.user)
    qs = _apply_text_filter(qs, q)
    qs = _apply_ui_filters(qs, ui_filters)
    qs = _with_metrics(qs)

    monthly = (
        qs.annotate(m=TruncMonth("trade_at"))
        .values("m")
        .annotate(
            pnl=Coalesce(
                Sum("pnl_jpy_calc", output_field=DEC2),
                Value(Decimal("0"), output_field=DEC2),
            ),
            cash_spec=Coalesce(
                Sum(
                    Case(
                        When(
                            account__in=["SPEC", "NISA"],
                            then=F("cashflow_calc_jpy"),
                        ),
                        default=Value(Decimal("0"), output_field=DEC2),
                        output_field=DEC2,
                    )
                ),
                Value(Decimal("0"), output_field=DEC2),
            ),
            cash_margin=Coalesce(
                Sum(
                    Case(
                        When(account="MARGIN", then=F("pnl_jpy_calc")),
                        default=Value(Decimal("0"), output_field=DEC2),
                        output_field=DEC2,
                    )
                ),
                Value(Decimal("0"), output_field=DEC2),
            ),
        )
        .order_by("m")
    )

    labels, pnl, cash, cash_spec, cash_margin, pnl_cum = [], [], [], [], [], []
    running = Decimal("0")
    for row in monthly:
        label = row["m"].strftime("%Y-%m") if row["m"] else ""
        labels.append(label)

        p = row["pnl"] or Decimal("0")
        cs = row["cash_spec"] or Decimal("0")
        cm = row["cash_margin"] or Decimal("0")
        ctotal = cs + cm

        pnl.append(float(p))
        cash.append(float(ctotal))
        cash_spec.append(float(cs))
        cash_margin.append(float(cm))

        running += p
        pnl_cum.append(float(running))

    return JsonResponse(
        {
            "labels": labels,
            "pnl": pnl,
            "pnl_cum": pnl_cum,
            "cash": cash,
            "cash_spec": cash_spec,
            "cash_margin": cash_margin,
        }
    )


@login_required
@require_GET
def realized_ranking_partial(request):
    ui_filters = _ui_filters_from_request(request)
    q = ui_filters["q"]
    freq = (request.GET.get("freq") or "month").lower()

    base = RealizedTrade.objects.filter(user=request.user)
    base = _apply_text_filter(base, q)
    base = _apply_ui_filters(base, ui_filters)

    if ui_filters.get("year"):
        start = end = None
        used_preset = "UI_FILTER"
    else:
        base, start, end, used_preset = _apply_period_filters(base, request, ui_filters)

    def build_rows(qs):
        qs = _with_metrics(qs)
        grouped = (
            qs.values("ticker", "name")
            .annotate(
                n=Coalesce(Count("id"), Value(0), output_field=IntegerField()),
                qty=Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),
                pnl=Coalesce(
                    Sum("pnl_jpy_calc", output_field=DEC2),
                    Value(Decimal("0"), output_field=DEC2),
                ),
                wins=Coalesce(
                    Sum(
                        Case(
                            When(pnl_jpy_calc__gt=0, then=1),
                            default=0,
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                    output_field=IntegerField(),
                ),
            )
        )
        rows = []
        for r in grouped:
            n = int(r["n"] or 0)
            wins = int(r["wins"] or 0)
            pnl_val = r["pnl"] or Decimal("0")
            rows.append(
                {
                    "ticker": r["ticker"],
                    "name": r["name"],
                    "n": n,
                    "qty": int(r["qty"] or 0),
                    "pnl": pnl_val,
                    "avg": (pnl_val / n) if n else Decimal("0"),
                    "win_rate": (wins * 100.0 / n) if n else 0.0,
                }
            )
        return rows

    rows = build_rows(base)

    if not rows:
        today = timezone.localdate()
        start_fb = (today.replace(day=1) - timezone.timedelta(days=365)).replace(day=1)
        end_fb = today
        fallback = RealizedTrade.objects.filter(user=request.user)
        fallback = _apply_text_filter(fallback, q)
        fallback = _apply_ui_filters(fallback, ui_filters)
        if not ui_filters.get("year"):
            fallback = fallback.filter(trade_at__gte=start_fb, trade_at__lte=end_fb)
        rows = build_rows(fallback)
        used_preset = "LAST_12M"

    top5 = sorted(rows, key=lambda x: (x["pnl"], x["win_rate"]), reverse=True)[:5]
    worst5 = sorted(rows, key=lambda x: (x["pnl"], -x["win_rate"]))[:5]

    ctx = {
        "top5": top5,
        "worst5": worst5,
        "preset": used_preset,
        "freq": freq,
        "start": start if not ui_filters.get("year") else None,
        "end": end if not ui_filters.get("year") else None,
        "q": q,
        "ui_filters": ui_filters,
    }
    return render(request, "realized/_ranking.html", ctx)


@login_required
@require_GET
def realized_ranking_detail_partial(request):
    ticker = (request.GET.get("ticker") or "").strip()
    ui_filters = _ui_filters_from_request(request)
    q = ui_filters["q"]

    if not ticker:
        return render(
            request,
            "realized/_ranking_detail.html",
            {"ticker": "", "rows": [], "agg": {}},
        )

    qs = RealizedTrade.objects.filter(user=request.user, ticker=ticker)
    qs = _apply_text_filter(qs, q)
    qs = _apply_ui_filters(qs, ui_filters)

    if not ui_filters.get("year"):
        qs, _, _, _ = _apply_period_filters(qs, request, ui_filters)

    qs = _with_metrics(qs).order_by("-trade_at", "-id")

    dec0 = Value(Decimal("0"), output_field=DEC2)

    agg = qs.aggregate(
        n=Coalesce(Count("id"), Value(0), output_field=IntegerField()),
        qty=Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),
        pnl=Coalesce(
            Sum(Coalesce(F("pnl_jpy_calc"), dec0), output_field=DEC2),
            dec0,
        ),
        wins=Coalesce(
            Sum(
                Case(
                    When(pnl_jpy_calc__gt=0, then=1),
                    default=0,
                    output_field=IntegerField(),
                )
            ),
            Value(0),
            output_field=IntegerField(),
        ),
    )

    n = agg.get("n") or 0
    wins = agg.get("wins") or 0
    agg["win_rate"] = (wins * 100.0 / n) if n else 0.0

    rows = list(qs[:5])

    return render(
        request,
        "realized/_ranking_detail.html",
        {
            "ticker": ticker,
            "rows": rows,
            "agg": agg,
        },
    )


@login_required
@require_GET
def list_page(request):
    ui_filters = _ui_filters_from_request(request)
    q = ui_filters["q"]

    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    qs = _apply_text_filter(qs, q)
    qs = _apply_ui_filters(qs, ui_filters)

    rows = _with_metrics(qs)
    agg = _decorate_agg_with_ai(_aggregate(qs), "総合")
    agg_brokers = _decorate_broker_aggs(_aggregate_by_broker(qs))

    return render(
        request,
        "realized/list.html",
        {
            "q": q,
            "trades": rows,
            "agg": agg,
            "agg_brokers": agg_brokers,
            "ui_filters": ui_filters,
            "available_years": _available_years_for_user(request.user),
            "month_choices": _month_choices(),
        },
    )


@login_required
@require_GET
def export_csv(request):
    ui_filters = _ui_filters_from_request(request)
    q = ui_filters["q"]

    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    qs = _apply_text_filter(qs, q)
    qs = _apply_ui_filters(qs, ui_filters)
    qs = _with_metrics(qs)

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    w = csv.writer(resp)
    w.writerow(
        [
            "trade_at",
            "opened_at",
            "ticker",
            "name",
            "sector33_code",
            "sector33_name",
            "side",
            "qty",
            "price",
            "basis",
            "fee",
            "tax",
            "cashflow_calc(現金)",
            "pnl_display(実損)",
            "pnl_jpy_calc(円実損)",
            "country",
            "currency",
            "open_fx_rate",
            "close_fx_rate",
            "fx_rate(compat)",
            "strategy_label",
            "policy_key",
            "is_ai_signal",
            "position_key",
            "broker",
            "account",
            "memo",
        ]
    )
    for t in qs:
        w.writerow(
            [
                t.trade_at,
                getattr(t, "opened_at", None) or "",
                t.ticker,
                smart_str(getattr(t, "name", "") or ""),
                smart_str(getattr(t, "sector33_code", "") or ""),
                smart_str(getattr(t, "sector33_name", "") or ""),
                t.side,
                t.qty,
                t.price,
                getattr(t, "basis", "") or "",
                t.fee,
                t.tax,
                getattr(t, "cashflow_calc", Decimal("0.00")),
                getattr(t, "pnl_display", Decimal("0.00")),
                getattr(t, "pnl_jpy_calc", Decimal("0.00")),
                smart_str(getattr(t, "country", "") or ""),
                smart_str(getattr(t, "currency", "") or ""),
                getattr(t, "open_fx_rate", "") or "",
                getattr(t, "close_fx_rate", "") or "",
                getattr(t, "fx_rate", "") or "",
                smart_str(getattr(t, "strategy_label", "") or ""),
                smart_str(getattr(t, "policy_key", "") or ""),
                "1" if getattr(t, "is_ai_signal", False) else "0",
                smart_str(getattr(t, "position_key", "") or ""),
                smart_str(getattr(t, "broker", "") or ""),
                smart_str(getattr(t, "account", "") or ""),
                smart_str(t.memo or ""),
            ]
        )
    return resp


def _parse_ymd(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


@login_required
@require_GET
def table_partial(request):
    import re

    try:
        ui_filters = _ui_filters_from_request(request)
        q = ui_filters["q"]

        ym_s = (request.GET.get("ym") or "").strip()
        start_s = (request.GET.get("start") or "").strip()
        end_s = (request.GET.get("end") or "").strip()
        accept = (request.headers.get("Accept") or "")
        want_json = (request.GET.get("format") == "json") or ("application/json" in accept)

        qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
        qs = _apply_text_filter(qs, q)
        qs = _apply_ui_filters(qs, ui_filters)

        if re.fullmatch(r"\d{4}-\d{2}", ym_s):
            y, m = map(int, ym_s.split("-"))
            qs = qs.filter(trade_at__year=y, trade_at__month=m)
        else:
            def _to_date(s: str, end_side: bool = False):
                if not s:
                    return None
                if len(s) == 7 and s.count("-") == 1:
                    yy, mm = map(int, s.split("-"))
                    if end_side:
                        if mm == 12:
                            return datetime(yy, 12, 31).date()
                        return (datetime(yy, mm + 1, 1) - timedelta(days=1)).date()
                    return datetime(yy, mm, 1).date()
                return parse_date(s)

            sd = _to_date(start_s, end_side=False)
            ed = _to_date(end_s, end_side=True)
            if sd and ed:
                qs = qs.filter(trade_at__range=(sd, ed))
            elif sd:
                qs = qs.filter(trade_at__gte=sd)
            elif ed:
                qs = qs.filter(trade_at__lte=ed)

        rows = _with_metrics(qs)
        html = render_to_string("realized/_table.html", {"trades": rows, "ui_filters": ui_filters}, request=request)

        if want_json:
            return JsonResponse({"ok": True, "html": html, "count": len(rows)})
        return HttpResponse(html)

    except Exception as e:
        logger.exception("table_partial error: %s", e)
        tb = traceback.format_exc()
        html = f"""
        <div class="p-3 rounded-lg" style="background:#2b1f24;color:#ffd1d1;border:1px solid #ff9aa9;">
          <div style="font-weight:700;margin-bottom:6px">テーブル取得に失敗しました</div>
          <div style="margin-bottom:8px">{str(e)}</div>
          <details style="font-size:12px;opacity:.85">
            <summary>詳細</summary>
            <pre style="white-space:pre-wrap">{tb}</pre>
          </details>
        </div>
        """
        if (request.GET.get("format") == "json") or ("application/json" in (request.headers.get("Accept") or "")):
            return JsonResponse({"ok": False, "html": html}, status=200)
        return HttpResponse(html, status=200)


@login_required
@require_GET
def summary_partial(request):
    try:
        ui_filters = _ui_filters_from_request(request)
        q = ui_filters["q"]

        qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
        qs = _apply_text_filter(qs, q)
        qs = _apply_ui_filters(qs, ui_filters)

        agg = _decorate_agg_with_ai(_aggregate(qs), "総合")
        agg_brokers = _decorate_broker_aggs(_aggregate_by_broker(qs))

        return render(
            request,
            "realized/_summary.html",
            {"agg": agg, "agg_brokers": agg_brokers, "q": q, "ui_filters": ui_filters},
        )
    except Exception as e:
        logger.exception("summary_partial error: %s", e)
        tb = traceback.format_exc()
        html = f"""
        <div class="p-3 rounded-lg" style="background:#2b1f24;color:#ffd1d1;border:1px solid #ff9aa9;">
          <div style="font-weight:700;margin-bottom:6px">サマリー取得に失敗しました</div>
          <div style="margin-bottom:8px">{str(e)}</div>
          <details style="font-size:12px;opacity:.85">
            <summary>詳細</summary>
            <pre style="white-space:pre-wrap">{tb}</pre>
          </details>
        </div>
        """
        return HttpResponse(html)


@login_required
@require_GET
def close_sheet(request, pk: int):
    try:
        holding_filters = {"pk": pk}
        if any(f.name == "user" for f in Holding._meta.fields):
            holding_filters["user"] = request.user
        h = get_object_or_404(Holding, **holding_filters)

        def g(obj, name, default=""):
            return getattr(obj, name, default) if obj is not None else default

        h_qty = g(h, "quantity", None)
        if h_qty in (None, ""):
            h_qty = g(h, "qty", 0)

        pre_broker = (g(h, "broker", "") or "OTHER")
        pre_account = (g(h, "account", "") or "SPEC")
        pre_country = (g(h, "market", "") or g(h, "country", "") or "JP").upper()
        pre_currency = (g(h, "currency", "") or "JPY").upper()

        side_qs = (request.GET.get("side") or "").upper()
        if side_qs not in ("SELL", "BUY"):
            side_qs = ""

        if not side_qs:
            holding_side = (g(h, "side", "BUY") or "BUY").upper()
            if holding_side == "BUY":
                initial_side = "SELL"
            elif holding_side == "SELL":
                initial_side = "BUY"
            else:
                initial_side = "SELL"
        else:
            initial_side = side_qs

        ctx = {
            "h": h,
            "h_qty": h_qty,
            "prefill": {
                "date": timezone.localdate().isoformat(),
                "ticker": g(h, "ticker", ""),
                "name": g(h, "name", ""),
                "broker": pre_broker,
                "account": pre_account,
            },
            "initial_side": initial_side,
            "currency": pre_currency,
            "country": pre_country,
        }

        html = render_to_string("realized/_close_sheet.html", ctx, request=request)
        return HttpResponse(html)

    except Exception as e:
        logger.exception("close_sheet error (pk=%s): %s", pk, e)
        tb = traceback.format_exc()
        error_html = f"""
        <div class="sheet" style="padding:16px">
          <div class="sheet-title" style="font-weight:700;margin-bottom:10px">クローズシートの表示に失敗しました</div>
          <div style="color:#fca5a5;margin-bottom:8px;">{str(e)}</div>
          <details style="font-size:12px;opacity:.8">
            <summary>詳細</summary>
            <pre style="white-space:pre-wrap">{tb}</pre>
          </details>
          <div style="margin-top:12px">
            <button type="button" data-dismiss="sheet"
                    style="padding:10px 12px;border:1px solid rgba(255,255,255,.2);border-radius:10px">
              閉じる
            </button>
          </div>
        </div>
        """
        return HttpResponse(error_html)