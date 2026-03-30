# [FILE] metrics_service.py
# [PATH] portfolio/services/trades/metrics_service.py
#
# このファイルは何？
# - 実現損益画面で使う集計・注釈ロジックをまとめた service
# - PnL / 現金フロー / 円換算 / 勝率 / PF / 平均保有日数などを担当する
#
# 今回の目的
# - views/realized.py から重い集計ロジックを切り離す
# - 一覧表示・月次集計・ランキング・チャートJSONで共通利用する

from __future__ import annotations

from decimal import Decimal

from django.db.models import (
    Count,
    Sum,
    F,
    Value,
    Case,
    When,
    ExpressionWrapper,
    DecimalField,
    IntegerField,
    Q,
    FloatField,
)
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date

DEC2 = DecimalField(max_digits=20, decimal_places=2)
DEC4 = DecimalField(max_digits=20, decimal_places=4)


def parse_period(request):
    """
    ?preset=THIS_MONTH|YTD|LAST_12M|THIS_YEAR|CUSTOM
    ?start=YYYY-MM-DD&end=YYYY-MM-DD （CUSTOM のときのみ）
    返り値: (start_date or None, end_date or None, preset)
    """
    preset = (request.GET.get("preset") or "THIS_MONTH").upper()
    today = timezone.localdate()

    if preset == "THIS_MONTH":
        start = today.replace(day=1)
        end = today
    elif preset == "THIS_YEAR":
        start = today.replace(month=1, day=1)
        end = today
    elif preset == "YTD":
        start = today.replace(month=1, day=1)
        end = today
    elif preset == "LAST_12M":
        start = (today.replace(day=1) - timezone.timedelta(days=365)).replace(day=1)
        end = today
    elif preset == "CUSTOM":
        s = parse_date(request.GET.get("start") or "")
        e = parse_date(request.GET.get("end") or "")
        start = s or None
        end = e or None
    else:
        start = today.replace(day=1)
        end = today
        preset = "THIS_MONTH"

    return start, end, preset


def parse_period_from_request(request):
    """
    summary_period_partial と同等の指定を受け取って期間を返す軽量版。
    start/end を優先。無ければ preset から解決（THIS_MONTH/THIS_YEAR/LAST_12M）。
    """
    from datetime import date, timedelta

    start_s = (request.GET.get("start") or "").strip()
    end_s = (request.GET.get("end") or "").strip()
    if start_s and end_s:
        try:
            y1, m1, d1 = [int(x) for x in start_s.split("-")]
            y2, m2, d2 = [int(x) for x in end_s.split("-")]
            return date(y1, m1, d1), date(y2, m2, d2)
        except Exception:
            pass

    today = timezone.localdate()
    first_day_this_month = today.replace(day=1)
    preset = (request.GET.get("preset") or "LAST_12M").upper()

    if preset == "THIS_MONTH":
        start = first_day_this_month
        if first_day_this_month.month == 12:
            end = first_day_this_month.replace(
                year=first_day_this_month.year + 1, month=1, day=1
            ) - timedelta(days=1)
        else:
            end = first_day_this_month.replace(
                month=first_day_this_month.month + 1, day=1
            ) - timedelta(days=1)
    elif preset == "THIS_YEAR":
        start = today.replace(month=1, day=1)
        end = today
    else:
        y = first_day_this_month.year
        m = first_day_this_month.month
        m_prev = ((m - 1) or 12)
        y_prev = (y - 1) if m == 1 else y
        start = first_day_this_month.replace(year=y_prev, month=m_prev, day=1)
        end = today
    return start, end


def with_metrics(qs):
    """
    現金・PnL・比率計算に必要な注釈を付与
    """
    dec0 = Value(Decimal("0"), output_field=DEC2)
    one = Value(Decimal("1"), output_field=DEC4)

    gross = ExpressionWrapper(F("qty") * F("price"), output_field=DEC2)
    fee = Coalesce(F("fee"), dec0)
    tax = Coalesce(F("tax"), dec0)

    cashflow_calc = Case(
        When(side="SELL", then=gross - fee - tax),
        When(side="BUY", then=-(gross + fee + tax)),
        default=Value(Decimal("0"), output_field=DEC2),
        output_field=DEC2,
    )

    pnl_display = Coalesce(F("cashflow"), Value(Decimal("0"), output_field=DEC2))
    basis_amount = ExpressionWrapper(F("basis") * F("qty"), output_field=DEC2)

    trade_pnl = ExpressionWrapper(
        (F("price") - F("basis")) * F("qty") - fee - tax,
        output_field=DEC2,
    )

    pnl_pct = Case(
        When(
            side="SELL",
            basis__gt=0,
            then=ExpressionWrapper(
                Cast(trade_pnl, FloatField()) * Value(100.0, output_field=FloatField())
                / Cast(basis_amount, FloatField()),
                output_field=FloatField(),
            ),
        ),
        default=None,
        output_field=FloatField(),
    )

    is_win = Case(When(pnl_display__gt=0, then=1), default=0, output_field=IntegerField())

    hold_days_f = Case(
        When(hold_days__isnull=False, then=Cast(F("hold_days"), FloatField())),
        default=None,
        output_field=FloatField(),
    )

    open_fx_to_jpy = Case(
        When(
            currency__iexact="USD",
            then=Coalesce(
                F("open_fx_rate"),
                F("fx_rate"),
                one,
            ),
        ),
        When(currency__iexact="JPY", then=one),
        default=one,
        output_field=DEC4,
    )

    close_fx_to_jpy = Case(
        When(
            currency__iexact="USD",
            then=Coalesce(
                F("close_fx_rate"),
                F("fx_rate"),
                one,
            ),
        ),
        When(currency__iexact="JPY", then=one),
        default=one,
        output_field=DEC4,
    )

    cashflow_calc_jpy = Case(
        When(side="BUY", then=ExpressionWrapper(cashflow_calc * open_fx_to_jpy, output_field=DEC2)),
        When(side="SELL", then=ExpressionWrapper(cashflow_calc * close_fx_to_jpy, output_field=DEC2)),
        default=ExpressionWrapper(cashflow_calc * close_fx_to_jpy, output_field=DEC2),
        output_field=DEC2,
    )

    yen_buy = ExpressionWrapper(F("basis") * F("qty") * open_fx_to_jpy, output_field=DEC2)
    yen_sell = ExpressionWrapper(F("price") * F("qty") * close_fx_to_jpy, output_field=DEC2)
    yen_fee_tax = ExpressionWrapper((fee + tax) * close_fx_to_jpy, output_field=DEC2)

    pnl_jpy_calc = Case(
        When(
            side="SELL",
            basis__isnull=False,
            basis__gt=0,
            then=ExpressionWrapper((yen_sell - yen_buy) - yen_fee_tax, output_field=DEC2),
        ),
        default=ExpressionWrapper(pnl_display * close_fx_to_jpy, output_field=DEC2),
        output_field=DEC2,
    )

    fx_to_jpy_calc = close_fx_to_jpy

    return qs.annotate(
        cashflow_calc=ExpressionWrapper(cashflow_calc, output_field=DEC2),
        pnl_display=ExpressionWrapper(pnl_display, output_field=DEC2),
        pnl_pct=pnl_pct,
        is_win=is_win,
        hold_days_f=hold_days_f,
        open_fx_to_jpy=open_fx_to_jpy,
        close_fx_to_jpy=close_fx_to_jpy,
        fx_to_jpy_calc=fx_to_jpy_calc,
        pnl_jpy_calc=pnl_jpy_calc,
        cashflow_calc_jpy=cashflow_calc_jpy,
    )


def aggregate(qs):
    """
    画面上部（大元）サマリー。
    すべて「円換算されたPnL / 現金」をベースに集計する。
    """
    qs = with_metrics(qs)
    dec0 = Value(Decimal("0"), output_field=DEC2)

    pnl_cnt = Case(
        When(
            Q(side="SELL")
            & Q(qty__gt=0)
            & Q(basis__isnull=False)
            & ~Q(basis=0)
            & Q(pnl_pct__isnull=False),
            then=1,
        ),
        default=0,
        output_field=IntegerField(),
    )
    hold_cnt = Case(When(hold_days_f__gt=0, then=1), default=0, output_field=IntegerField())

    agg = qs.aggregate(
        n=Coalesce(Count("id"), Value(0), output_field=IntegerField()),
        fee=Coalesce(Sum(Coalesce(F("fee"), dec0)), dec0),
        wins=Coalesce(Sum("is_win", output_field=IntegerField()), Value(0), output_field=IntegerField()),
        pnl=Coalesce(Sum("pnl_jpy_calc", output_field=DEC2), dec0),
        profit_sum=Coalesce(
            Sum(
                Case(
                    When(pnl_jpy_calc__gt=0, then=F("pnl_jpy_calc")),
                    default=dec0,
                    output_field=DEC2,
                )
            ),
            dec0,
        ),
        loss_sum=Coalesce(
            Sum(
                Case(
                    When(pnl_jpy_calc__lt=0, then=F("pnl_jpy_calc")),
                    default=dec0,
                    output_field=DEC2,
                )
            ),
            dec0,
        ),
        pnl_pct_sum=Coalesce(
            Sum(
                Case(
                    When(pnl_pct__isnull=False, then=F("pnl_pct")),
                    default=None,
                    output_field=FloatField(),
                )
            ),
            Value(0.0, output_field=FloatField()),
        ),
        pnl_pct_cnt=Coalesce(Sum(pnl_cnt), Value(0), output_field=IntegerField()),
        hold_days_sum=Coalesce(
            Sum(
                Case(
                    When(hold_days_f__gt=0, then=F("hold_days_f")),
                    default=None,
                    output_field=FloatField(),
                )
            ),
            Value(0.0, output_field=FloatField()),
        ),
        hold_days_cnt=Coalesce(Sum(hold_cnt), Value(0), output_field=IntegerField()),
        cash_spec=Coalesce(
            Sum(
                Case(
                    When(
                        account__in=["SPEC", "NISA"],
                        then=F("cashflow_calc_jpy"),
                    ),
                    default=dec0,
                    output_field=DEC2,
                )
            ),
            dec0,
        ),
        cash_margin=Coalesce(
            Sum(
                Case(
                    When(account="MARGIN", then=F("pnl_jpy_calc")),
                    default=dec0,
                    output_field=DEC2,
                )
            ),
            dec0,
        ),
    )

    n = int(agg.get("n") or 0)
    wins = int(agg.get("wins") or 0)
    agg["win_rate"] = (wins * 100.0 / n) if n else 0.0

    profit = Decimal(agg.get("profit_sum") or 0)
    loss = Decimal(agg.get("loss_sum") or 0)
    loss_abs = abs(loss)
    agg["pf"] = (profit / loss_abs) if loss_abs else (Decimal("Infinity") if profit > 0 else None)

    p_sum = float(agg.get("pnl_pct_sum") or 0.0)
    p_cnt = int(agg.get("pnl_pct_cnt") or 0)
    agg["avg_pnl_pct"] = (p_sum / p_cnt) if p_cnt else None

    h_sum = float(agg.get("hold_days_sum") or 0.0)
    h_cnt = int(agg.get("hold_days_cnt") or 0)
    agg["avg_hold_days"] = (h_sum / h_cnt) if h_cnt else None

    agg["cash_total"] = (agg.get("cash_spec") or Decimal("0")) + (agg.get("cash_margin") or Decimal("0"))
    return agg


def aggregate_by_broker(qs):
    """
    証券会社別サマリー。
    すべて円換算（pnl_jpy_calc / cashflow_calc_jpy）で集計。
    """
    qs = with_metrics(qs)
    dec0 = Value(Decimal("0"), output_field=DEC2)

    pnl_cnt = Case(
        When(
            Q(side="SELL")
            & Q(qty__gt=0)
            & Q(basis__isnull=False)
            & ~Q(basis=0)
            & Q(pnl_pct__isnull=False),
            then=1,
        ),
        default=0,
        output_field=IntegerField(),
    )
    hold_cnt = Case(When(hold_days_f__gt=0, then=1), default=0, output_field=IntegerField())

    rows = (
        qs.values("broker")
        .annotate(
            n=Coalesce(Count("id"), Value(0), output_field=IntegerField()),
            wins=Coalesce(Sum("is_win", output_field=IntegerField()), Value(0), output_field=IntegerField()),
            pnl=Coalesce(Sum("pnl_jpy_calc", output_field=DEC2), dec0),
            fee=Coalesce(Sum(Coalesce(F("fee"), dec0)), dec0),
            cash_spec=Coalesce(
                Sum(
                    Case(
                        When(
                            account__in=["SPEC", "NISA"],
                            then=F("cashflow_calc_jpy"),
                        ),
                        default=dec0,
                        output_field=DEC2,
                    )
                ),
                dec0,
            ),
            cash_margin=Coalesce(
                Sum(
                    Case(
                        When(account="MARGIN", then=F("pnl_jpy_calc")),
                        default=dec0,
                        output_field=DEC2,
                    )
                ),
                dec0,
            ),
            profit_sum=Coalesce(
                Sum(
                    Case(
                        When(pnl_jpy_calc__gt=0, then=F("pnl_jpy_calc")),
                        default=dec0,
                        output_field=DEC2,
                    )
                ),
                dec0,
            ),
            loss_sum=Coalesce(
                Sum(
                    Case(
                        When(pnl_jpy_calc__lt=0, then=F("pnl_jpy_calc")),
                        default=dec0,
                        output_field=DEC2,
                    )
                ),
                dec0,
            ),
            pnl_pct_sum=Coalesce(
                Sum(
                    Case(
                        When(pnl_pct__isnull=False, then=F("pnl_pct")),
                        default=None,
                        output_field=FloatField(),
                    )
                ),
                Value(0.0, output_field=FloatField()),
            ),
            pnl_pct_cnt=Coalesce(Sum(pnl_cnt), Value(0), output_field=IntegerField()),
            hold_days_sum=Coalesce(
                Sum(
                    Case(
                        When(hold_days_f__gt=0, then=F("hold_days_f")),
                        default=None,
                        output_field=FloatField(),
                    )
                ),
                Value(0.0, output_field=FloatField()),
            ),
            hold_days_cnt=Coalesce(Sum(hold_cnt), Value(0), output_field=IntegerField()),
        )
        .order_by("broker")
    )

    out = []
    for r in rows:
        d = dict(r)
        n = int(d.get("n") or 0)
        wins = int(d.get("wins") or 0)
        d["win_rate"] = (wins * 100.0 / n) if n else 0.0

        ps = float(d.get("pnl_pct_sum") or 0.0)
        pc = int(d.get("pnl_pct_cnt") or 0)
        d["avg_pnl_pct"] = (ps / pc) if pc else None

        hs = float(d.get("hold_days_sum") or 0.0)
        hc = int(d.get("hold_days_cnt") or 0)
        d["avg_hold_days"] = (hs / hc) if hc else None

        profit = Decimal(d.get("profit_sum") or 0)
        loss = Decimal(d.get("loss_sum") or 0)
        loss_abs = abs(loss)
        d["pf"] = (profit / loss_abs) if loss_abs else (Decimal("Infinity") if profit > 0 else None)
        d["cash_total"] = (d.get("cash_spec") or Decimal("0")) + (d.get("cash_margin") or Decimal("0"))

        out.append(d)
    return out