# portfolio/views/realized.py
from __future__ import annotations

from decimal import Decimal
from datetime import date as _date, timedelta as _timedelta
from datetime import timedelta, datetime
import csv
import logging
import traceback
import time
import yfinance as yf
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import FloatField
from django.db.models import (
    Count, Sum, F, Value, Case, When, ExpressionWrapper,
    DecimalField, IntegerField, Q, CharField, Avg
)
from django.db.models import DecimalField as DField
from django.db.models.functions import Abs, Coalesce, TruncMonth, TruncYear, Cast
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.utils.encoding import smart_str
from django.utils.dateparse import parse_date

from ..models import Holding, RealizedTrade

logger = logging.getLogger(__name__)

# 証券会社の表示名マッピング
BROKER_LABELS = {
    "MATSUI":  "松井証券",
    "RAKUTEN": "楽天証券",
    "SBI":     "SBI証券",
}

# ============================================================
#  ユーティリティ
# ============================================================
DEC2 = DecimalField(max_digits=20, decimal_places=2)
DEC4 = DecimalField(max_digits=20, decimal_places=4)


def _to_dec(v, default="0"):
    try:
        return Decimal(str(v if v not in (None, "") else default))
    except Exception:
        return Decimal(default)


# 期間ヘルパ
def _parse_period(request):
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
        # 前年同日+1で12ヶ月（ざっくり：日数は気にせず概算でOK）
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


def _parse_ymd(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


# ============================================================
#  注釈（テーブル/サマリー兼用）
#    - cashflow_calc         : 現金の受渡 (+受取/-支払)  ※税は fee に含める前提
#         SELL:  qty*price - fee
#         BUY : -(qty*price + fee)
#    - pnl_display           : “投資家PnL”として画面に出す手入力の実損（= モデルの cashflow）
#    - fx_to_jpy             : 1通貨あたり何円か
#    - pnl_jpy_calc          : 円換算した投資家PnL
#    - cashflow_calc_jpy     : 円換算した受渡キャッシュフロー
# ============================================================

def _with_metrics(qs):
    """
    現金・PnL・比率計算に必要な注釈を付与
    """
    dec0 = Value(Decimal("0"), output_field=DEC2)
    one = Value(Decimal("1"), output_field=DEC4)

    gross = ExpressionWrapper(F("qty") * F("price"), output_field=DEC2)
    fee = Coalesce(F("fee"), dec0)
    tax = Coalesce(F("tax"), dec0)

    # 現金フロー（受渡ベース / 通貨建て）
    cashflow_calc = Case(
        When(side="SELL", then=gross - fee - tax),
        When(side="BUY", then=-(gross + fee + tax)),
        default=Value(Decimal("0"), output_field=DEC2),
        output_field=DEC2,
    )

    # 表示用PnL（通貨建ての「投資家PnL」）
    pnl_display = Coalesce(F("cashflow"), Value(Decimal("0"), output_field=DEC2))

    # 分母: basis * qty
    basis_amount = ExpressionWrapper(F("basis") * F("qty"), output_field=DEC2)

    # 分子: (price - basis) * qty - fee - tax
    trade_pnl = ExpressionWrapper(
        (F("price") - F("basis")) * F("qty") - fee - tax,
        output_field=DEC2,
    )

    # Float にキャストして割り算（通貨建て％）
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

    # 勝敗
    is_win = Case(When(pnl_display__gt=0, then=1), default=0, output_field=IntegerField())

    # 保有日数
    hold_days_f = Case(
        When(hold_days__isnull=False, then=Cast(F("hold_days"), FloatField())),
        default=None,
        output_field=FloatField(),
    )

    # --- ここから円換算 ------------------------------------------------
    # fx_to_jpy_calc: 1通貨あたり何円か
    fx_to_jpy_calc = Case(
        # USD で fx_rate が入っている行 → その値を採用
        When(
            currency__iexact="USD",
            fx_rate__isnull=False,
            fx_rate__gt=0,
            then=F("fx_rate"),
        ),
        # JPY または fx_rate 未設定 → そのまま1倍
        When(currency__iexact="JPY", then=one),
        default=one,
        output_field=DEC4,
    )

    # 円換算PnL / 現金
    pnl_jpy_calc = ExpressionWrapper(pnl_display * fx_to_jpy_calc, output_field=DEC2)
    cashflow_calc_jpy = ExpressionWrapper(cashflow_calc * fx_to_jpy_calc, output_field=DEC2)

    return qs.annotate(
        cashflow_calc=ExpressionWrapper(cashflow_calc, output_field=DEC2),
        pnl_display=ExpressionWrapper(pnl_display, output_field=DEC2),
        pnl_pct=pnl_pct,
        is_win=is_win,
        hold_days_f=hold_days_f,
        fx_to_jpy_calc=fx_to_jpy_calc,
        pnl_jpy_calc=pnl_jpy_calc,
        cashflow_calc_jpy=cashflow_calc_jpy,
    )


# ============================================================
#  サマリー（二軸＋口座区分）
#   - fee        : 手数料合計
#   - cash_spec  : 💰現金フロー（現物/NISA）= cashflow_calc_jpy を合計
#   - cash_margin: 💰現金フロー（信用）    = pnl_jpy_calc を合計
#   - cash_total : 上記の合計
#   - pnl        : 📈PnL累計 = pnl_jpy_calc を合計
# ============================================================
def _aggregate(qs):
    """
    画面上部（大元）サマリー。
    すべて「円換算されたPnL / 現金」をベースに集計する。
    """
    qs = _with_metrics(qs)
    dec0 = Value(Decimal("0"), output_field=DEC2)

    # “平均の対象” を数えるフラグ
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
        # 件数/手数料（手数料はそのまま通貨建ての合計だが、金額としては小さいのでそのまま）
        n=Coalesce(Count("id"), Value(0), output_field=IntegerField()),
        fee=Coalesce(Sum(Coalesce(F("fee"), dec0)), dec0),

        # 勝率用（pnl_display の符号で判定だが、円換算でも符号は同じ）
        wins=Coalesce(Sum("is_win", output_field=IntegerField()), Value(0), output_field=IntegerField()),

        # 📈PnL 累計（円ベース）
        pnl=Coalesce(Sum("pnl_jpy_calc", output_field=DEC2), dec0),

        # 利益合計・損失合計（円ベース）
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

        # 平均PnL% 用（％そのものは通貨に依存しないので、従来ロジックのまま）
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

        # 平均保有日数
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

        # 💰現金（円ベース）
        #  現物/NISA: 受渡キャッシュフローの円換算
        #  信用     : 投資家PnL（pnl_jpy_calc）をそのまま現金相当として扱う
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

    # ---- 後計算（Python） ----
    n = int(agg.get("n") or 0)
    wins = int(agg.get("wins") or 0)
    agg["win_rate"] = (wins * 100.0 / n) if n else 0.0

    # PF（損失は負なので絶対値で割る）
    profit = Decimal(agg.get("profit_sum") or 0)
    loss = Decimal(agg.get("loss_sum") or 0)
    loss_abs = abs(loss)
    agg["pf"] = (profit / loss_abs) if loss_abs else (Decimal("Infinity") if profit > 0 else None)

    # 平均PnL% / 平均保有日数
    p_sum = float(agg.get("pnl_pct_sum") or 0.0)
    p_cnt = int(agg.get("pnl_pct_cnt") or 0)
    agg["avg_pnl_pct"] = (p_sum / p_cnt) if p_cnt else None

    h_sum = float(agg.get("hold_days_sum") or 0.0)
    h_cnt = int(agg.get("hold_days_cnt") or 0)
    agg["avg_hold_days"] = (h_sum / h_cnt) if h_cnt else None

    # 💰現金合計（円ベース）
    agg["cash_total"] = (agg.get("cash_spec") or Decimal("0")) + (agg.get("cash_margin") or Decimal("0"))
    return agg


def _aggregate_by_broker(qs):
    """
    証券会社別サマリー。
    すべて円換算（pnl_jpy_calc / cashflow_calc_jpy）で集計。
    """
    qs = _with_metrics(qs)
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

            # 円換算PnL
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

            # 平均用の分子/分母
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

        # 平均PnL% / 平均保有日数
        ps, pc = float(d.get("pnl_pct_sum") or 0.0), int(d.get("pnl_pct_cnt") or 0)
        d["avg_pnl_pct"] = (ps / pc) if pc else None

        hs, hc = float(d.get("hold_days_sum") or 0.0), int(d.get("hold_days_cnt") or 0)
        d["avg_hold_days"] = (hs / hc) if hc else None

        # PF / 現金合計（円ベース）
        profit = Decimal(d.get("profit_sum") or 0)
        loss = Decimal(d.get("loss_sum") or 0)
        loss_abs = abs(loss)
        d["pf"] = (profit / loss_abs) if loss_abs else (Decimal("Infinity") if profit > 0 else None)
        d["cash_total"] = (d.get("cash_spec") or Decimal("0")) + (d.get("cash_margin") or Decimal("0"))

        out.append(d)
    return out


# --- 期間まとめ（部分テンプレ） -------------------------
def _parse_period_from_request(request):
    """
    summary_period_partial と同等の指定を受け取って期間を返す軽量版。
    start/end を優先。無ければ preset から解決（THIS_MONTH/THIS_YEAR/LAST_12M）。
    """
    from datetime import date, timedelta

    # 明示指定があればそれを使う
    start_s = (request.GET.get("start") or "").strip()
    end_s = (request.GET.get("end") or "").strip()
    if start_s and end_s:
        try:
            y1, m1, d1 = [int(x) for x in start_s.split("-")]
            y2, m2, d2 = [int(x) for x in end_s.split("-")]
            return date(y1, m1, d1), date(y2, m2, d2)
        except Exception:
            pass

    # preset でざっくり
    today = timezone.localdate()
    first_day_this_month = today.replace(day=1)
    preset = (request.GET.get("preset") or "LAST_12M").upper()

    if preset == "THIS_MONTH":
        start = first_day_this_month
        # 月末
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
    else:  # LAST_12M など
        # 12ヶ月前の翌日〜今日
        y = first_day_this_month.year
        m = first_day_this_month.month
        m_prev = ((m - 1) or 12)
        y_prev = (y - 1) if m == 1 else y
        start = first_day_this_month.replace(year=y_prev, month=m_prev, day=1)
        end = today
    return start, end


@login_required
@require_GET
def monthly_kpis_partial(request):
    """
    月別のKPI（平均実現損益(%) / 勝率 / PF / 平均保有日数）を返す。
    ※ BUY/SELL 両方あってもフィルタ期間内の SELL を対象に集計。
    ※ PnL・PF は「円換算済みPnL」で計算する。
    """
    q = (request.GET.get("q") or "").strip()
    start, end = _parse_period_from_request(request)

    qs = RealizedTrade.objects.filter(user=request.user, trade_at__range=(start, end))
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    # 為替・PnL注釈
    qs = _with_metrics(qs)

    total = 0
    win = 0
    pnl_pos = Decimal("0")
    pnl_neg = Decimal("0")
    pct_list = []
    hold_list = []

    for t in qs:
        # 円換算済みPnL
        cf_jpy = Decimal(str(getattr(t, "pnl_jpy_calc", Decimal("0")) or 0))

        if cf_jpy > 0:
            pnl_pos += cf_jpy
        elif cf_jpy < 0:
            pnl_neg += cf_jpy  # 負のまま

        # 勝率は SELL のみカウント
        if t.side == "SELL":
            total += 1
            if cf_jpy > 0:
                win += 1

            # %: basis×qty が正なら計算（％なので通貨に依存しない）
            try:
                if t.basis is not None and t.qty and Decimal(str(t.qty)) > 0:
                    denom = Decimal(str(t.basis)) * Decimal(str(t.qty))
                    if denom > 0:
                        pct_list.append((cf_jpy / denom) * Decimal("100"))
            except Exception:
                pass

        # 平均保有日数
        if t.hold_days is not None:
            try:
                hd = int(t.hold_days)
                if hd >= 0:
                    hold_list.append(hd)
            except Exception:
                pass

    # KPI 値
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
    """
    期間内のブローカー別 / 口座区分別のブレークダウン。
    PnL は円換算済みPnL（pnl_jpy_calc）の合計。
    """
    q = (request.GET.get("q") or "").strip()
    start, end = _parse_period_from_request(request)

    qs = RealizedTrade.objects.filter(user=request.user, trade_at__range=(start, end))
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

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
    """
    月別 PnL の Top3 / Worst3 を返す部分テンプレ。
    - PnL は 円換算済みPnL（pnl_jpy_calc）の合計
    - 期間は preset/start/end（_summary_period と同じ名前）を優先
    - 期間指定が無ければ直近365日
    """
    q = (request.GET.get("q") or "").strip()

    qs = RealizedTrade.objects.all()
    if any(f.name == "user" for f in RealizedTrade._meta.fields):
        qs = qs.filter(user=request.user)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    # ---- 期間 ----
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
    """
    指定の year/month の日次ヒートマップ用 JSON を返す。
    - pnl: その日の “投資家PnL”（= pnl_jpy_calc）の合計（円）
    - cash_spec: 現物/NISA の現金フロー合計（cashflow_calc_jpy）
    - cash_margin: 信用の現金相当（pnl_jpy_calc）
    """
    q = (request.GET.get("q") or "").strip()

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
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

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
    """
    月別サマリーの専用ページ。
    本体は空のコンテナを出すだけで、内容は _summary_period.html を
    preset=LAST_12M & freq=month で HTMX 取得して差し込む。
    既存の期間サマリー部分テンプレをそのまま使うので、既存画面は壊れない。
    """
    q = (request.GET.get("q") or "").strip()
    ctx = {
        "q": q,
        # デフォルト表示は「過去12ヶ月 × 月次」
        "default_preset": "LAST_12M",
        "default_freq": "month",
    }
    return render(request, "realized/monthly.html", ctx)


@login_required
@require_GET
def summary_period_partial(request):
    """
    月次/年次で 📈PnL と 💰現金（現物/信用/合計）を集計して返す。
    パラメータ:
      - preset=THIS_MONTH|THIS_YEAR|LAST_12M|YTD|CUSTOM
      - start/end（CUSTOM のみ）
      - freq=month|year（既定: month）
      - focus=YYYY-MM または YYYY（行ハイライト用ラベル）
      - keep=all のときは focus しても全体表は維持（単独絞り込みしない）

    ※ ここでは「円換算済み」の値（pnl_jpy_calc / cashflow_calc_jpy）だけを使う。
    """
    from django.db.models.functions import TruncMonth, TruncYear
    from django.db.models import Count, Sum, Value, IntegerField, Q, F
    from decimal import Decimal

    q     = (request.GET.get("q") or "").strip()
    freq  = (request.GET.get("freq") or "month").lower()
    focus = (request.GET.get("focus") or "").strip()
    keep  = (request.GET.get("keep") or "").lower()

    # 期間の解釈
    start, end, preset = _parse_period(request)

    qs = RealizedTrade.objects.filter(user=request.user)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    if start:
        qs = qs.filter(trade_at__gte=start)
    if end:
        qs = qs.filter(trade_at__lte=end)

    # ★ 円換算用メトリクスを付与（pnl_jpy_calc / cashflow_calc_jpy など）
    qs = _with_metrics(qs)

    # バケット
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
              n   = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
              qty = Coalesce(Sum("qty"),  Value(0), output_field=IntegerField()),
              fee = Coalesce(
                  Sum(
                      Coalesce(
                          F("fee"),
                          Value(Decimal("0"), output_field=DEC2)
                      )
                  ),
                  Value(Decimal("0"), output_field=DEC2),
              ),

              # 💰現物/NISA = 受渡キャッシュフロー（円換算）
              cash_spec = Coalesce(
                  Sum(
                      "cashflow_calc_jpy",
                      filter=Q(account__in=["SPEC", "NISA"]),
                      output_field=DEC2,
                  ),
                  Value(Decimal("0"), output_field=DEC2),
              ),
              # 💰信用 = 投資家PnL（円換算）
              cash_margin = Coalesce(
                  Sum(
                      "pnl_jpy_calc",              # ★ ここを pnl_jpy → pnl_jpy_calc に修正
                      filter=Q(account="MARGIN"),
                      output_field=DEC2,
                  ),
                  Value(Decimal("0"), output_field=DEC2),
              ),

              # 📈PnL も円換算済み（全口座合計）
              pnl = Coalesce(
                  Sum("pnl_jpy_calc", output_field=DEC2),  # ★ ここも pnl_jpy → pnl_jpy_calc
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
            "label":  label,
            "n":      r["n"],
            "qty":    r["qty"],
            "fee":    r["fee"],
            "cash_spec":   r["cash_spec"],
            "cash_margin": r["cash_margin"],
            "cash_total":  cash_total,
            "pnl":    r["pnl"],
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
    }
    return render(request, "realized/_summary_period.html", ctx)


@login_required
def realized_summary_partial(request):
    """
    サマリー（全体＋ブローカー別）を部分描画して返す。
    """
    q = (request.GET.get("q") or "").strip()

    qs = RealizedTrade.objects.filter(user=request.user).order_by(
        "-trade_at", "-id"
    )
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    agg = _aggregate(qs)
    agg_brokers = _aggregate_by_broker(qs)

    return render(
        request,
        "realized/_summary.html",
        {"agg": agg, "agg_brokers": agg_brokers, "q": q},
    )


# --- 月次サマリー（Chart.js 用 JSON） -------------------------
@login_required
@require_GET
def chart_monthly_json(request):
    """
    月次で集計して JSON 返却。
    - pnl:    各月の “投資家PnL”（= pnl_jpy_calc 合計）
    - cash:   各月の “現金フロー”（現物/NISA=受渡円、信用=円換算PnL）
    """
    q = (request.GET.get("q") or "").strip()

    qs = RealizedTrade.objects.filter(user=request.user)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

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
    """
    銘柄別ランキング（期間連動）
    - PnL は円換算済みPnL（pnl_jpy_calc）の合計
    - 今月/指定期間で0件なら、自動で「直近12か月」にフォールバック
    """
    q = (request.GET.get("q") or "").strip()
    start, end, preset = _parse_period(request)
    freq = (request.GET.get("freq") or "month").lower()

    base = RealizedTrade.objects.filter(user=request.user)
    if q:
        base = base.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    def apply_period(qs, s, e):
        if s:
            qs = qs.filter(trade_at__gte=s)
        if e:
            qs = qs.filter(trade_at__lte=e)
        return qs

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

    rows = build_rows(apply_period(base, start, end))
    used_preset = preset

    if not rows:
        today = timezone.localdate()
        start_fb = (today.replace(day=1) - timezone.timedelta(days=365)).replace(day=1)
        end_fb = today
        rows = build_rows(apply_period(base, start_fb, end_fb))
        used_preset = "LAST_12M"

    top5 = sorted(rows, key=lambda x: (x["pnl"], x["win_rate"]), reverse=True)[:5]
    worst5 = sorted(rows, key=lambda x: (x["pnl"], -x["win_rate"]))[:5]

    ctx = {
        "top5": top5,
        "worst5": worst5,
        "preset": used_preset,
        "freq": freq,
        "start": start,
        "end": end,
        "q": q,
    }
    return render(request, "realized/_ranking.html", ctx)


@login_required
@require_GET
def realized_ranking_detail_partial(request):
    """
    銘柄ドリルダウン（期間連動）
    GET: ticker, q, preset/freq/start/end
    返却: _ranking_detail.html
    PnL は円換算済みPnL（pnl_jpy_calc）で集計。
    """
    ticker = (request.GET.get("ticker") or "").strip()
    q = (request.GET.get("q") or "").strip()
    start, end, preset = _parse_period(request)

    if not ticker:
        return render(
            request,
            "realized/_ranking_detail.html",
            {"ticker": "", "rows": [], "agg": {}},
        )

    qs = RealizedTrade.objects.filter(user=request.user, ticker=ticker)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
    if start:
        qs = qs.filter(trade_at__gte=start)
    if end:
        qs = qs.filter(trade_at__lte=end)

    qs = _with_metrics(qs).order_by("-trade_at", "-id")

    dec0 = Value(Decimal("0"), output_field=DEC2)

    agg = qs.aggregate(
        n=Coalesce(Count("id"), Value(0), output_field=IntegerField()),
        qty=Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),
        pnl=Coalesce(
            Sum(Coalesce(F("pnl_jpy_calc"), dec0), output_field=DEC2),
            dec0,
        ),
        avg=Coalesce(
            Avg(Coalesce(F("pnl_jpy_calc"), dec0), output_field=DEC2),
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

    rows = list(qs[:5])  # 直近5件（rows側はテンプレで pnl_jpy_calc も使える）

    return render(
        request,
        "realized/_ranking_detail.html",
        {
            "ticker": ticker,
            "rows": rows,
            "agg": agg,
        },
    )


# ============================================================
#  画面
# ============================================================

@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by(
        "-trade_at", "-id"
    )
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_metrics(qs)
    agg = _aggregate(qs)
    agg_brokers = _aggregate_by_broker(qs)

    return render(
        request,
        "realized/list.html",
        {
            "q": q,
            "trades": rows,
            "agg": agg,
            "agg_brokers": agg_brokers,
        },
    )


# ============================================================
#  作成
#   - pnl_input を “手入力の実損（投資家PnL）” として cashflow に保存
#   - fee はそのまま保存（現金計算に利用）
# ============================================================
@login_required
@require_POST
def create(request):
    date_raw = (request.POST.get("date") or "").strip()
    try:
        trade_at = (
            timezone.datetime.fromisoformat(date_raw).date()
            if date_raw
            else timezone.localdate()
        )
    except Exception:
        trade_at = timezone.localdate()

    ticker = (request.POST.get("ticker") or "").strip()
    name = (request.POST.get("name") or "").strip()
    side = (request.POST.get("side") or "SELL").upper()
    broker = (request.POST.get("broker") or "OTHER").upper()
    account = (request.POST.get("account") or "SPEC").upper()

    try:
        qty = int(request.POST.get("qty") or 0)
    except Exception:
        qty = 0

    price = _to_dec(request.POST.get("price"))
    fee = _to_dec(request.POST.get("fee"))
    tax = _to_dec(request.POST.get("tax"))
    pnl_input = _to_dec(request.POST.get("pnl_input"))
    memo = (request.POST.get("memo") or "").strip()

    # 🔸 解析用の付加情報（POSTに無ければデフォルトでOK）
    opened_raw = (request.POST.get("opened_at") or "").strip()
    sector33_code = (request.POST.get("sector33_code") or "").strip()
    sector33_name = (request.POST.get("sector33_name") or "").strip()
    country_in = (request.POST.get("country") or "").strip().upper()
    currency_in = (request.POST.get("currency") or "").strip().upper()
    fx_rate_raw = (request.POST.get("fx_rate") or "").strip()
    strategy_label = (request.POST.get("strategy_label") or "").strip()
    policy_key = (request.POST.get("policy_key") or "").strip()
    is_ai_raw = (request.POST.get("is_ai_signal") or "").strip().lower()
    position_key = (request.POST.get("position_key") or "").strip()

    # デフォルト補正
    country = country_in or "JP"
    currency = currency_in or "JPY"

    fx_rate = None
    if fx_rate_raw not in ("", None):
        try:
            fx_rate = _to_dec(fx_rate_raw)
        except Exception:
            fx_rate = None
    # ⚠️ ここでは自動取得しない：証券会社レートと合わせるため「完全手入力」

    is_ai_signal = is_ai_raw in ["1", "true", "on", "yes"]

    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)
    if side not in ("SELL", "BUY"):
        return JsonResponse({"ok": False, "error": "Sideが不正です"}, status=400)

    # BUYは basis=price を保存（SELLは逆算）
    basis = None
    if side == "SELL" and qty > 0:
        try:
            basis_calc = price - (pnl_input + fee + tax) / Decimal(qty)
            basis = basis_calc if basis_calc > 0 else None
        except Exception:
            basis = None
    elif side == "BUY":
        basis = price

    # 保有開始日 / 保有日数
    opened_at = None
    hold_days = None
    try:
        hd_raw = (request.POST.get("hold_days") or "").strip()
        if hd_raw != "":
            hold_days = max(int(hd_raw), 0)

        if opened_raw:
            opened_at = timezone.datetime.fromisoformat(opened_raw).date()
            if hold_days is None:
                hold_days = max((trade_at - opened_at).days, 0)
    except Exception:
        opened_at = None
        hold_days = hold_days if hold_days is not None else None

    # ポジションキー（未指定なら簡易自動生成）
    if not position_key:
        if opened_at:
            position_key = f"{ticker}-{opened_at.isoformat()}-{account}"
        else:
            position_key = f"{ticker}-{trade_at.isoformat()}-{account}"

    RealizedTrade.objects.create(
        user=request.user,
        trade_at=trade_at,
        side=side,
        ticker=ticker,
        name=name,
        broker=broker,
        account=account,
        qty=qty,
        price=price,
        fee=fee,
        tax=tax,
        cashflow=pnl_input,
        basis=basis,
        hold_days=hold_days,
        memo=memo,
        # 追加フィールド
        opened_at=opened_at,
        sector33_code=sector33_code,
        sector33_name=sector33_name,
        country=country,
        currency=currency,
        fx_rate=fx_rate,  # ← ここは「入力されたものだけ」保存
        strategy_label=strategy_label,
        policy_key=policy_key,
        is_ai_signal=is_ai_signal,
        position_key=position_key,
    )

    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by(
        "-trade_at", "-id"
    )
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_metrics(qs)
    agg = _aggregate(qs)

    table_html = render_to_string(
        "realized/_table.html", {"trades": rows}, request=request
    )
    summary_html = render_to_string(
        "realized/_summary.html", {"agg": agg}, request=request
    )
    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})


# ============================================================
#  削除（テーブル＋サマリーを同時更新して返す）
#  ★ CashLedger の紐づく行も同時削除に対応した完全版
# ============================================================
@login_required
@require_POST
def delete(request, pk: int):
    """
    RealizedTrade を削除する際に、
    1) RealizedTrade (pk) を削除
    2) CashLedger の source_type=REALIZED かつ source_id=pk を全削除
    3) テーブルとサマリーを再描画して返す（HTMX）
    """

    # --- RealizedTrade が存在するかチェック（存在しなくてもLedgerクリーンのため取る） ---
    trade = RealizedTrade.objects.filter(pk=pk, user=request.user).first()

    # --- Ledger 削除 ---
    try:
        from ..models_cash import CashLedger
        if trade:
            CashLedger.objects.filter(
                source_type=CashLedger.SourceType.REALIZED,
                source_id=trade.id,
            ).delete()
    except Exception:
        # Ledgerモデル未使用の環境でも落ちないように防御
        pass

    # --- RealizedTrade 削除 ---
    RealizedTrade.objects.filter(pk=pk, user=request.user).delete()

    # --- 再描画 ---
    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by(
        "-trade_at", "-id"
    )
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains?q))

    rows = _with_metrics(qs)
    agg = _aggregate(qs)

    table_html = render_to_string(
        "realized/_table.html", {"trades": rows}, request=request
    )
    summary_html = render_to_string(
        "realized/_summary.html", {"agg": agg}, request=request
    )

    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})

# ============================================================
#  CSV（両方を出力：現金ベースと手入力PnL）
# ============================================================
@login_required
@require_GET
def export_csv(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by(
        "-trade_at", "-id"
    )
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
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
            "fee",
            "tax",
            "cashflow_calc(現金)",
            "pnl_display(実損)",
            "country",
            "currency",
            "fx_rate",
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
                t.fee,
                t.tax,
                getattr(t, "cashflow_calc", Decimal("0.00")),
                getattr(t, "pnl_display", Decimal("0.00")),
                smart_str(getattr(t, "country", "") or ""),
                smart_str(getattr(t, "currency", "") or ""),
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


# ============================================================
#  部分テンプレ
# ============================================================

def _parse_ymd(s: str):
    """
    'YYYY-MM-DD' 文字列 -> date。失敗時 None。
    """
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


@login_required
@require_GET
def table_partial(request):
    """
    明細テーブル（部分描画）
      - ym=YYYY-MM があれば最優先でその月のみ
      - それ以外は start/end（YYYY-MM / YYYY-MM-DD）でフォールバック
      - format=json のとき {ok, html, count}
    """
    import re

    try:
        q = (request.GET.get("q") or "").strip()
        ym_s = (request.GET.get("ym") or "").strip()
        start_s = (request.GET.get("start") or "").strip()
        end_s = (request.GET.get("end") or "").strip()
        accept = (request.headers.get("Accept") or "")
        want_json = (request.GET.get("format") == "json") or (
            "application/json" in accept
        )

        qs = RealizedTrade.objects.filter(user=request.user).order_by(
            "-trade_at", "-id"
        )
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

        # --- ym が来たら最優先で固定 ---
        if re.fullmatch(r"\d{4}-\d{2}", ym_s):
            y, m = map(int, ym_s.split("-"))
            qs = qs.filter(trade_at__year=y, trade_at__month=m)
        else:
            # --- start/end フォールバック ---
            def _to_date(s: str, end_side: bool = False):
                if not s:
                    return None
                if len(s) == 7 and s.count("-") == 1:  # YYYY-MM
                    yy, mm = map(int, s.split("-"))
                    if end_side:
                        # 月末
                        if mm == 12:
                            return date(yy, 12, 31)
                        return date(yy, mm + 1, 1) - timedelta(days=1)
                    return date(yy, mm, 1)
                return parse_date(s)

            sd = _to_date(start_s, end_side=False)
            ed = _to_date(end_s, end_side=True)
            if sd and ed:
                qs = (
                    qs.filter(trade_at__date__range=(sd, ed))
                    if qs.model._meta.get_field("trade_at")
                    .get_internal_type()
                    .lower()
                    .startswith("date")
                    is False
                    else qs.filter(trade_at__range=(sd, ed))
                )
            elif sd:
                qs = (
                    qs.filter(trade_at__date__gte=sd)
                    if qs.model._meta.get_field("trade_at")
                    .get_internal_type()
                    .lower()
                    .startswith("date")
                    is False
                    else qs.filter(trade_at__gte=sd)
                )
            elif ed:
                qs = (
                    qs.filter(trade_at__date__lte=ed)
                    if qs.model._meta.get_field("trade_at")
                    .get_internal_type()
                    .lower()
                    .startswith("date")
                    is False
                    else qs.filter(trade_at__lte=ed)
                )

        rows = _with_metrics(qs)
        html = render_to_string(
            "realized/_table.html", {"trades": rows}, request=request
        )

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
        if (request.GET.get("format") == "json") or (
            "application/json" in (request.headers.get("Accept") or "")
        ):
            return JsonResponse({"ok": False, "html": html}, status=200)
        return HttpResponse(html, status=200)


@login_required
@require_GET
def summary_partial(request):
    try:
        q = (request.GET.get("q") or "").strip()
        qs = RealizedTrade.objects.filter(user=request.user).order_by(
            "-trade_at", "-id"
        )
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
        agg = _aggregate(qs)
        agg_brokers = _aggregate_by_broker(qs)
        return render(
            request,
            "realized/_summary.html",
            {"agg": agg, "agg_brokers": agg_brokers, "q": q},
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
        return HttpResponse(html)  # ★200で返す


# ============================================================
#  保有 → 売却（ボトムシート／登録）
#   ※ 実損（投資家PnL）の逆算は行わず、fee は入力値を採用
#      → いまは close_submit で basis から fee を逆算する仕様に更新済み
# ============================================================
@login_required
@require_GET
def close_sheet(request, pk: int):
    """
    保有 → 売却/買付のボトムシート（名前は close のまま）
    - 既定は「保有サイドの反対側」を初期タブにする（BUY→SELL / SELL→BUY）
    - クエリ ?side=SELL|BUY があればそちらを優先
    """
    try:
        # --- Holding 取得（user フィールド有無の両対応）---
        holding_filters = {"pk": pk}
        if any(f.name == "user" for f in Holding._meta.fields):
            holding_filters["user"] = request.user
        h = get_object_or_404(Holding, **holding_filters)

        def g(obj, name, default=""):
            return getattr(obj, name, default) if obj is not None else default

        # quantity / qty 両対応
        h_qty = g(h, "quantity", None)
        if h_qty in (None, ""):
            h_qty = g(h, "qty", 0)

        # プリセット：broker / account / 国・通貨
        pre_broker = (g(h, "broker", "") or "OTHER")
        pre_account = (g(h, "account", "") or "SPEC")
        pre_country = (g(h, "market", "") or g(h, "country", "") or "JP").upper()
        pre_currency = (g(h, "currency", "") or "JPY").upper()

        # 1) ?side= があればそれを最優先
        side_qs = (request.GET.get("side") or "").upper()
        if side_qs not in ("SELL", "BUY"):
            side_qs = ""

        # 2) 無指定なら「保有サイドの反対側」を初期サイドにする
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
            # 通貨情報だけテンプレに渡す（FXレートは完全手入力）
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


@login_required
@require_POST
@transaction.atomic
def close_submit(request, pk: int):
    """
    保有行のクローズ（SELL/BUY 両対応）
    - 反対売買のみ受け付け、数量を減算。0で保有を自動削除。
    - 同方向(例: 保有SELLに対してSELL)はクローズ不可。
    - 手数料は basis と “投資家PnL(cashflow)” と 税 から逆算。
      * 保有BUY→SELL: fee = (price - basis) * qty - pnl_input - tax
      * 保有SELL→BUY: fee = (basis - price) * qty - pnl_input - tax
    """
    try:
        # --- Holding 取得（行ロック & user 有無両対応） ---
        filters = {"pk": pk}
        if any(f.name == "user" for f in Holding._meta.fields):
            filters["user"] = request.user
        h = Holding.objects.select_for_update().get(**filters)

        def h_get(name, default=None):
            return getattr(h, name, default)

        # --- 入力 ---
        date_raw = (request.POST.get("date") or "").strip()
        try:
            trade_at = (
                timezone.datetime.fromisoformat(date_raw).date()
                if date_raw
                else timezone.localdate()
            )
        except Exception:
            trade_at = timezone.localdate()

        side_in = (request.POST.get("side") or "").upper()
        if side_in not in ("SELL", "BUY"):
            return JsonResponse(
                {"ok": False, "error": "side が不正です（SELL/BUY）"}, status=400
            )

        try:
            qty_in = int(request.POST.get("qty") or 0)
        except Exception:
            qty_in = 0

        price = _to_dec(request.POST.get("price"))
        tax_in = _to_dec(request.POST.get("tax"))  # 任意（未入力なら0）
        cashflow_in = request.POST.get("cashflow")  # 投資家PnL（±・任意）
        pnl_input = None if cashflow_in in (None, "") else _to_dec(cashflow_in)

        broker = (request.POST.get("broker") or "OTHER").upper()
        account = (request.POST.get("account") or "SPEC").upper()
        memo = (request.POST.get("memo") or "").strip()
        name = (request.POST.get("name") or "").strip() or h_get("name", "") or ""

        # 追加情報
        # ★ A案：POSTされていれば優先しつつ、なければ Holding.sector を使う
        sector33_code_in = (request.POST.get("sector33_code") or "").strip()
        sector33_name_in = (request.POST.get("sector33_name") or "").strip()
        country_in = (request.POST.get("country") or "").strip().upper()
        currency_in = (request.POST.get("currency") or "").strip().upper()
        fx_rate_raw = (request.POST.get("fx_rate") or "").strip()
        strategy_label_in = (request.POST.get("strategy_label") or "").strip()
        policy_key_in = (request.POST.get("policy_key") or "").strip()
        is_ai_raw = (request.POST.get("is_ai_signal") or "").strip().lower()
        position_key_in = (request.POST.get("position_key") or "").strip()

        # --- 保有数量 ---
        held_qty = h_get("quantity", None)
        if held_qty is None:
            held_qty = h_get("qty", 0)

        # --- バリデーション ---
        if qty_in <= 0 or price <= 0:
            return JsonResponse(
                {"ok": False, "error": "数量/価格を確認してください"}, status=400
            )

        holding_side = (h_get("side", "BUY") or "BUY").upper()
        is_opposite = (holding_side == "BUY" and side_in == "SELL") or (
            holding_side == "SELL" and side_in == "BUY"
        )
        if not is_opposite:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "同方向の注文はクローズではありません。反対売買を選択してください。",
                },
                status=400,
            )

        if qty_in > held_qty:
            return JsonResponse(
                {"ok": False, "error": "保有数量を超えています"}, status=400
            )

        # --- basis 取得（保有から推定） ---
        basis = None
        for fname in [
            "avg_cost",
            "average_cost",
            "avg_price",
            "average_price",
            "basis",
            "cost_price",
            "cost_per_share",
            "avg",
            "average",
            "avg_unit_cost",
            "avg_purchase_price",
        ]:
            v = h_get(fname, None)
            if v not in (None, ""):
                try:
                    basis = Decimal(str(v))
                    break
                except Exception:
                    pass

        # 投資家PnL未入力なら 0
        if pnl_input is None:
            pnl_input = Decimal("0")

        # --- 手数料の逆算（税も考慮する） ---
        if basis is None:
            fee = Decimal("0")
        else:
            if holding_side == "BUY" and side_in == "SELL":
                fee = (price - basis) * Decimal(qty_in) - pnl_input - tax_in
            elif holding_side == "SELL" and side_in == "BUY":
                fee = (basis - price) * Decimal(qty_in) - pnl_input - tax_in
            else:
                fee = Decimal("0")

        # --- 保有開始日 / 保有日数算出 ---
        opened_date = None
        days_held = None
        try:
            opened_date = h_get("opened_at", None)
            if opened_date is None:
                created = h_get("created_at", None)
                if created:
                    opened_date = (
                        created.date() if hasattr(created, "date") else created
                    )
            if opened_date:
                days_held = max((trade_at - opened_date).days, 0)
        except Exception:
            opened_date = None
            days_held = None

        # --- 33業種 / 国・通貨 / FX / 戦略まわりを最終決定 ---
        # ★ A案：sector は Holding.sector をそのまま利用
        sector33_name = sector33_name_in or h_get("sector", "") or ""
        sector33_code = sector33_code_in or ""  # コードは今のところ保持していないので任意

        country = country_in or (h_get("country", "") or h_get("market", "") or "JP")
        currency = currency_in or (h_get("currency", "") or "JPY")

        fx_rate = None
        if fx_rate_raw not in ("", None):
            try:
                fx_rate = _to_dec(fx_rate_raw)
            except Exception:
                fx_rate = None
        else:
            fx_attr = h_get("fx_rate", None)
            if fx_attr not in (None, ""):
                try:
                    fx_rate = Decimal(str(fx_attr))
                except Exception:
                    fx_rate = None
        # ⚠️ ここでも自動取得はしない：入力 or 保有にあるものだけ

        strategy_label = strategy_label_in or h_get("strategy_label", "") or ""
        policy_key = policy_key_in or h_get("policy_key", "") or ""

        if is_ai_raw in ["1", "true", "on", "yes"]:
            is_ai_signal = True
        elif is_ai_raw in ["0", "false", "off", "no"]:
            is_ai_signal = False
        else:
            is_ai_signal = bool(h_get("is_ai_signal", False))

        ticker_val = h_get("ticker", "")
        position_key = position_key_in or h_get("position_key", "") or ""
        if not position_key:
            if opened_date:
                position_key = f"{ticker_val}-{opened_date.isoformat()}-{account}"
            else:
                position_key = f"{ticker_val}-{account}"

        # --- RealizedTrade 作成 ---
        rt_kwargs = dict(
            trade_at=trade_at,
            side=side_in,
            ticker=ticker_val,
            name=name,
            broker=broker,
            account=account,
            qty=qty_in,
            price=price,
            fee=fee,
            tax=tax_in,
            cashflow=pnl_input,
            basis=basis,
            hold_days=days_held,
            memo=memo,
            opened_at=opened_date,
            sector33_code=sector33_code,
            sector33_name=sector33_name,
            country=country,
            currency=currency,
            fx_rate=fx_rate,  # ← 証券会社レートを手入力したもの / 保有からの引継ぎのみ
            strategy_label=strategy_label,
            policy_key=policy_key,
            is_ai_signal=is_ai_signal,
            position_key=position_key,
        )
        if any(f.name == "user" for f in RealizedTrade._meta.fields):
            rt_kwargs["user"] = request.user
        RealizedTrade.objects.create(**rt_kwargs)

        # --- 保有数量の更新 ---
        if hasattr(h, "quantity"):
            h.quantity = F("quantity") - qty_in
            h.save(update_fields=["quantity"])
            h.refresh_from_db()
            if h.quantity <= 0:
                h.delete()
        else:
            h.qty = F("qty") - qty_in
            h.save(update_fields=["qty"])
            h.refresh_from_db()
            if h.qty <= 0:
                h.delete()

        # --- 再描画 ---
        q = (request.POST.get("q") or "").strip()
        qs = RealizedTrade.objects.all()
        if any(f.name == "user" for f in RealizedTrade._meta.fields):
            qs = qs.filter(user=request.user)
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
        qs = qs.order_by("-trade_at", "-id")

        rows = _with_metrics(qs)
        agg = _aggregate(qs)

        table_html = render_to_string(
            "realized/_table.html", {"trades": rows}, request=request
        )
        summary_html = render_to_string(
            "realized/_summary.html", {"agg": agg, "q": q}, request=request
        )

        if request.headers.get("HX-Request") == "true":
            return JsonResponse(
                {"ok": True, "table": table_html, "summary": summary_html}
            )
        else:
            from django.shortcuts import redirect

            return redirect("realized_list")

    except Exception as e:
        import traceback

        if request.headers.get("HX-Request") == "true":
            return JsonResponse(
                {
                    "ok": False,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                },
                status=400,
            )
        from django.shortcuts import redirect

        return redirect("realized_list")