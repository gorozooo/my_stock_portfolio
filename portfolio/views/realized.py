# portfolio/views/realized.py
from __future__ import annotations

from decimal import Decimal
from datetime import date as _date, timedelta as _timedelta
from datetime import timedelta
import csv
import logging
import traceback

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
#    - cashflow_calc: 現金の受渡 (+受取/-支払)  ※税は fee に含める前提
#         SELL:  qty*price - fee
#         BUY : -(qty*price + fee)
#    - pnl_display : “投資家PnL”として画面に出す手入力の実損（= モデルの cashflow を流用）
# ============================================================

def _with_metrics(qs):
    """
    現金・PnL・比率計算に必要な注釈を付与
    """
    dec0 = Value(Decimal("0"), output_field=DEC2)

    gross = ExpressionWrapper(F("qty") * F("price"), output_field=DEC2)
    fee   = Coalesce(F("fee"), dec0)
    tax   = Coalesce(F("tax"), dec0)

    # 現金フロー（受渡ベース）
    cashflow_calc = Case(
        When(side="SELL", then=gross - fee - tax),
        When(side="BUY",  then=-(gross + fee + tax)),
        default=Value(Decimal("0"), output_field=DEC2),
        output_field=DEC2,
    )

    # 表示用PnL
    pnl_display = Coalesce(F("cashflow"), Value(Decimal("0"), output_field=DEC2))

    # 分母: basis * qty
    basis_amount = ExpressionWrapper(F("basis") * F("qty"), output_field=DEC2)

    # 分子: (price - basis) * qty - fee - tax
    trade_pnl = ExpressionWrapper(
        (F("price") - F("basis")) * F("qty") - fee - tax,
        output_field=DEC2,
    )

    # Float にキャストして割り算
    pnl_pct = Case(
        When(side="SELL", basis__gt=0,
             then=ExpressionWrapper(
                 Cast(trade_pnl, FloatField()) * Value(100.0, output_field=FloatField()) /
                 Cast(basis_amount, FloatField()),
                 output_field=FloatField()
             )),
        default=None,
        output_field=FloatField(),
    )

    # 勝敗
    is_win = Case(When(pnl_display__gt=0, then=1), default=0, output_field=IntegerField())

    # 保有日数
    hold_days_f = Case(
        When(hold_days__isnull=False, then=Cast(F("hold_days"), FloatField())),
        default=None, output_field=FloatField()
    )

    return qs.annotate(
        cashflow_calc=ExpressionWrapper(cashflow_calc, output_field=DEC2),
        pnl_display=ExpressionWrapper(pnl_display, output_field=DEC2),
        pnl_pct=pnl_pct,
        is_win=is_win,
        hold_days_f=hold_days_f,
    )
    

# ============================================================
#  サマリー（二軸＋口座区分）
#   - fee        : 手数料合計
#   - cash_spec  : 💰現金フロー（現物/NISA）= cashflow_calc を合計
#   - cash_margin: 💰現金フロー（信用）    = 手入力PnL(cashflow) を合計
#   - cash_total : 上記の合計
#   - pnl        : 📈PnL累計 = 手入力PnL(cashflow) を合計
# ============================================================
def _aggregate(qs):
    """
    画面上部（大元）サマリー。
    ※ Avg は使わず、平均は Sum/件数 を Python 後計算。
    """
    qs = _with_metrics(qs)
    dec0 = Value(Decimal("0"), output_field=DEC2)

    # “平均の対象” を数えるフラグ
    pnl_cnt  = Case(
        When(
            Q(side="SELL") & Q(qty__gt=0) &
            Q(basis__isnull=False) & ~Q(basis=0) &
            Q(pnl_pct__isnull=False),
            then=1
        ),
        default=0,
        output_field=IntegerField(),
    )
    hold_cnt = Case(When(hold_days_f__gt=0, then=1), default=0, output_field=IntegerField())

    agg = qs.aggregate(
        # 件数/手数料
        n   = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
        fee = Coalesce(Sum(Coalesce(F("fee"), dec0)), dec0),

        # 勝率用
        wins = Coalesce(Sum("is_win", output_field=IntegerField()), Value(0), output_field=IntegerField()),

        # PnL 累計（表示用 = 手入力PnL）
        pnl = Coalesce(Sum("pnl_display", output_field=DEC2), dec0),

        # 利益合計・損失合計
        profit_sum = Coalesce(
            Sum(Case(When(pnl_display__gt=0, then=F("pnl_display")), default=dec0, output_field=DEC2)), dec0
        ),
        loss_sum   = Coalesce(
            Sum(Case(When(pnl_display__lt=0, then=F("pnl_display")), default=dec0, output_field=DEC2)), dec0
        ),

        # 平均PnL% 用（分子=PnL%合計、分母=対象件数）
        pnl_pct_sum = Coalesce(
            Sum(Case(When(pnl_pct__isnull=False, then=F("pnl_pct")), default=None, output_field=FloatField())),
            Value(0.0, output_field=FloatField())
        ),
        pnl_pct_cnt = Coalesce(Sum(pnl_cnt), Value(0), output_field=IntegerField()),

        # 平均保有日数 用（分子=日数合計、分母=対象件数）※0日は除外
        hold_days_sum = Coalesce(
            Sum(Case(When(hold_days_f__gt=0, then=F("hold_days_f")), default=None, output_field=FloatField())),
            Value(0.0, output_field=FloatField())
        ),
        hold_days_cnt = Coalesce(Sum(hold_cnt), Value(0), output_field=IntegerField()),

        # 現金（現物/NISA は受渡、信用は手入力PnL）
        cash_spec   = Coalesce(
            Sum(Case(When(account__in=["SPEC","NISA"], then=F("cashflow_calc")), default=dec0, output_field=DEC2)), dec0
        ),
        cash_margin = Coalesce(
            Sum(Case(When(account="MARGIN", then=F("pnl_display")), default=dec0, output_field=DEC2)), dec0
        ),
    )

    # ---- 後計算（Python） ----
    n = int(agg.get("n") or 0)
    wins = int(agg.get("wins") or 0)
    agg["win_rate"] = (wins * 100.0 / n) if n else 0.0

    # PF（損失は負なので絶対値で割る）
    profit = Decimal(agg.get("profit_sum") or 0)
    loss   = Decimal(agg.get("loss_sum") or 0)
    loss_abs = abs(loss)
    agg["pf"] = (profit / loss_abs) if loss_abs else (Decimal("Infinity") if profit > 0 else None)

    # 平均PnL% / 平均保有日数
    p_sum = float(agg.get("pnl_pct_sum") or 0.0)
    p_cnt = int(agg.get("pnl_pct_cnt") or 0)
    agg["avg_pnl_pct"] = (p_sum / p_cnt) if p_cnt else None

    h_sum = float(agg.get("hold_days_sum") or 0.0)
    h_cnt = int(agg.get("hold_days_cnt") or 0)
    agg["avg_hold_days"] = (h_sum / h_cnt) if h_cnt else None

    # 現金合計
    agg["cash_total"] = (agg.get("cash_spec") or Decimal("0")) + (agg.get("cash_margin") or Decimal("0"))
    return agg


def _aggregate_by_broker(qs):
    """
    証券会社別サマリー。
    ※ Avg は使わず Sum/件数で手計算。
    """
    qs = _with_metrics(qs)
    dec0 = Value(Decimal("0"), output_field=DEC2)

    pnl_cnt  = Case(
        When(
            Q(side="SELL") & Q(qty__gt=0) &
            Q(basis__isnull=False) & ~Q(basis=0) &
            Q(pnl_pct__isnull=False),
            then=1
        ),
        default=0,
        output_field=IntegerField(),
    )
    hold_cnt = Case(When(hold_days_f__gt=0, then=1), default=0, output_field=IntegerField())

    rows = (
        qs.values("broker")
          .annotate(
              n    = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
              wins = Coalesce(Sum("is_win", output_field=IntegerField()), Value(0), output_field=IntegerField()),

              pnl  = Coalesce(Sum("pnl_display", output_field=DEC2), dec0),
              fee  = Coalesce(Sum(Coalesce(F("fee"), dec0)), dec0),

              cash_spec   = Coalesce(
                  Sum(Case(When(account__in=["SPEC","NISA"], then=F("cashflow_calc")), default=dec0, output_field=DEC2)), dec0
              ),
              cash_margin = Coalesce(
                  Sum(Case(When(account="MARGIN", then=F("pnl_display")),   default=dec0, output_field=DEC2)), dec0
              ),

              profit_sum = Coalesce(
                  Sum(Case(When(pnl_display__gt=0, then=F("pnl_display")), default=dec0, output_field=DEC2)), dec0
              ),
              loss_sum   = Coalesce(
                  Sum(Case(When(pnl_display__lt=0, then=F("pnl_display")), default=dec0, output_field=DEC2)), dec0
              ),

              # 平均用の分子/分母（Avgは使わない）
              pnl_pct_sum = Coalesce(
                  Sum(Case(When(pnl_pct__isnull=False, then=F("pnl_pct")), default=None, output_field=FloatField())),
                  Value(0.0, output_field=FloatField())
              ),
              pnl_pct_cnt = Coalesce(Sum(pnl_cnt), Value(0), output_field=IntegerField()),

              hold_days_sum = Coalesce(
                  Sum(Case(When(hold_days_f__gt=0, then=F("hold_days_f")), default=None, output_field=FloatField())),
                  Value(0.0, output_field=FloatField())
              ),
              hold_days_cnt = Coalesce(Sum(hold_cnt), Value(0), output_field=IntegerField()),
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

        # PF / 現金合計
        profit = Decimal(d.get("profit_sum") or 0)
        loss   = Decimal(d.get("loss_sum") or 0)
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
    end_s   = (request.GET.get("end")   or "").strip()
    if start_s and end_s:
        try:
            y1,m1,d1 = [int(x) for x in start_s.split("-")]
            y2,m2,d2 = [int(x) for x in end_s.split("-")]
            return date(y1,m1,d1), date(y2,m2,d2)
        except Exception:
            pass

    # preset でざっくり
    today  = timezone.localdate()
    first_day_this_month = today.replace(day=1)
    preset = (request.GET.get("preset") or "LAST_12M").upper()

    if preset == "THIS_MONTH":
        start = first_day_this_month
        # 月末
        if first_day_this_month.month == 12:
            end = first_day_this_month.replace(year=first_day_this_month.year+1, month=1, day=1) - timedelta(days=1)
        else:
            end = first_day_this_month.replace(month=first_day_this_month.month+1, day=1) - timedelta(days=1)
    elif preset == "THIS_YEAR":
        start = today.replace(month=1, day=1)
        end   = today
    else:  # LAST_12M など
        # 12ヶ月前の翌日〜今日
        y = first_day_this_month.year
        m = first_day_this_month.month
        m_prev = ((m - 1) or 12)
        y_prev = (y - 1) if m == 1 else y
        start  = first_day_this_month.replace(year=y_prev, month=m_prev, day=1)
        end    = today
    return start, end


@login_required
@require_GET
def monthly_kpis_partial(request):
    """
    月別のKPI（平均実現損益(%) / 勝率 / PF / 平均保有日数）を返す。
    ※ BUY/SELL 両方あってもフィルタ期間内の SELL を対象に集計。
    ※ %の平均はトレードごとの％の単純平均（basis×qty が妥当なもののみ）。
    """
    q = (request.GET.get("q") or "").strip()
    start, end = _parse_period_from_request(request)

    qs = RealizedTrade.objects.filter(
        user=request.user,
        trade_at__range=(start, end)
    )
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    # 集計
    total = 0
    win   = 0
    pnl_pos = Decimal("0")
    pnl_neg = Decimal("0")
    pct_list = []
    hold_list = []

    for t in qs:
        # 勝率/PF は cashflow（あなたの“投資家PnL”）を使用
        cf = Decimal(str(t.cashflow or 0))
        if cf > 0:
            pnl_pos += cf
        elif cf < 0:
            pnl_neg += cf  # 負のまま

        # 勝率は SELL のみカウント（BUY は仕込段階想定）
        if t.side == "SELL":
            total += 1
            if cf > 0:
                win += 1
            # %: basis×qty が正なら計算
            try:
                if t.basis is not None and t.qty and Decimal(str(t.qty)) > 0:
                    denom = Decimal(str(t.basis)) * Decimal(str(t.qty))
                    if denom > 0:
                        pct_list.append((cf / denom) * Decimal("100"))
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
    pf      = (float(pnl_pos) / abs(float(pnl_neg))) if pnl_neg != 0 else None
    avg_hold= (sum(hold_list) / len(hold_list)) if hold_list else None

    ctx = {
        "avg_pct":   float(avg_pct) if avg_pct is not None else None,
        "winrate":   float(winrate) if winrate is not None else None,
        "pf":        float(pf) if pf is not None else None,
        "avg_hold":  float(avg_hold) if avg_hold is not None else None,
    }
    return render(request, "realized/_month_kpis.html", ctx)


@login_required
@require_GET
def monthly_breakdown_partial(request):
    """
    期間内のブローカー別 / 口座区分別のブレークダウン。
    """
    q = (request.GET.get("q") or "").strip()
    start, end = _parse_period_from_request(request)

    qs = RealizedTrade.objects.filter(
        user=request.user,
        trade_at__range=(start, end)
    )
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    # ブローカー表示名マップ
    broker_label = dict(RealizedTrade.BROKER_CHOICES)
    acct_label   = dict(RealizedTrade.ACCOUNT_CHOICES)

    # values で集計（PnL=cashflow の合計、件数）
    brokers = (
        qs.values("broker")
          .annotate(n=Count("id"), pnl=Sum("cashflow"))
          .order_by("broker")
    )
    accounts = (
        qs.values("account")
          .annotate(n=Count("id"), pnl=Sum("cashflow"))
          .order_by("account")
    )

    brokers_view = [
        {"label": broker_label.get(row["broker"], row["broker"]),
         "pnl": float(row["pnl"] or 0), "n": row["n"]}
        for row in brokers
    ]
    accounts_view = [
        {"label": acct_label.get(row["account"], row["account"]),
         "pnl": float(row["pnl"] or 0), "n": row["n"]}
        for row in accounts
    ]

    return render(request, "realized/_month_breakdown.html", {
        "brokers": brokers_view,
        "accounts": accounts_view,
    })

@login_required
@require_GET
def monthly_topworst_partial(request):
    """
    月別 PnL の Top3 / Worst3 を返す部分テンプレ。
    - PnL は cashflow 合計
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
    end_raw   = (request.GET.get("end") or "").strip()

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
        # preset が来ていればそれを解釈
        if preset == "THIS_MONTH":
            start = today.replace(day=1)
            end   = today
        elif preset == "THIS_YEAR":
            start = today.replace(month=1, day=1)
            end   = today
        elif preset == "LAST_12M":
            # 直近365日で代用（カバー範囲は十分）
            start = today - timedelta(days=365)
            end   = today
        else:
            start = today - timedelta(days=365)
            end   = today

    qs = qs.filter(trade_at__gte=start, trade_at__lte=end)

    dec0 = Value(0, output_field=DEC2)

    monthly = (
        qs.annotate(m=TruncMonth("trade_at"))
          .values("m")
          .annotate(pnl=Coalesce(Sum("cashflow", output_field=DEC2), dec0))
          .order_by("m")
    )

    items = []
    for r in monthly:
        dt = r["m"]
        label = dt.strftime("%Y-%m") if dt else ""
        items.append({"label": label, "pnl": float(r.get("pnl") or 0)})

    # 値が0のみのときはそのまま使う（Topだけでも出す）
    top   = sorted(items, key=lambda x: x["pnl"], reverse=True)[:3]
    worst = sorted(items, key=lambda x: x["pnl"])[:3]

    return render(request, "realized/_monthly_topworst.html", {"top": top, "worst": worst})

@login_required
@require_GET
def chart_daily_heat_json(request, year: int, month: int):
    """
    指定の year/month の日次ヒートマップ用 JSON を返す。
    - pnl: その日の “投資家PnL”（= pnl_display）の合計
    - cash_spec: 現物/NISA の現金フロー合計（cashflow_calc）
    - cash_margin: 信用の現金相当（pnl_display）合計
    """
    q = (request.GET.get("q") or "").strip()

    # 期間境界 (start <= trade_at < next_first)
    try:
        start = _date(int(year), int(month), 1)
    except Exception:
        # 不正な月は今月を返す
        start = timezone.localdate().replace(day=1)

    if start.month == 12:
        next_first = _date(start.year + 1, 1, 1)
    else:
        next_first = _date(start.year, start.month + 1, 1)

    # ベースQS
    qs = RealizedTrade.objects.filter(user=request.user,
                                      trade_at__gte=start,
                                      trade_at__lt=next_first)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    # 必要注釈を付与
    qs = _with_metrics(qs)

    # 日付ごとに集計
    daily = (
        qs.values("trade_at")
          .annotate(
              pnl = Coalesce(Sum("pnl_display", output_field=DEC2),
                             Value(Decimal("0"), output_field=DEC2)),
              cash_spec = Coalesce(
                  Sum("cashflow_calc", filter=Q(account__in=["SPEC","NISA"]), output_field=DEC2),
                  Value(Decimal("0"), output_field=DEC2)
              ),
              cash_margin = Coalesce(
                  Sum("pnl_display", filter=Q(account="MARGIN"), output_field=DEC2),
                  Value(Decimal("0"), output_field=DEC2)
              ),
          )
          .order_by("trade_at")
    )

    # JSON 形式へ
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
        labels and pnl.append(pf)
        cash_spec.append(float(cs))
        cash_margin.append(float(cm))

        vmin = pf if vmin is None else min(vmin, pf)
        vmax = pf if vmax is None else max(vmax, pf)

    return JsonResponse({
        "year": start.year,
        "month": start.month,
        "labels": labels,        # ["2025-09-01", ...]
        "pnl": pnl,              # 日次PnL（表示用）
        "cash_spec": cash_spec,  # 現物/NISA
        "cash_margin": cash_margin,  # 信用
        "min": vmin if vmin is not None else 0.0,
        "max": vmax if vmax is not None else 0.0,
    })

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

    # ✨ keep=all の場合は「単独月への絞り込み」はしない
    if start:
        qs = qs.filter(trade_at__gte=start)
    if end:
        qs = qs.filter(trade_at__lte=end)

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
              fee = Coalesce(Sum(Coalesce(F("fee"), Value(Decimal("0"), output_field=DEC2))),
                             Value(Decimal("0"), output_field=DEC2)),
              cash_spec   = Coalesce(Sum("cashflow_calc", filter=Q(account__in=["SPEC","NISA"]), output_field=DEC2),
                                     Value(Decimal("0"), output_field=DEC2)),
              cash_margin = Coalesce(Sum("cashflow_calc", filter=Q(account="MARGIN"), output_field=DEC2),
                                     Value(Decimal("0"), output_field=DEC2)),
              pnl = Coalesce(Sum("pnl_display", output_field=DEC2),
                             Value(Decimal("0"), output_field=DEC2)),
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
        "focus": focus if selected else "",  # 該当が無ければフォーカス解除
        "selected": selected,
    }
    return render(request, "realized/_summary_period.html", ctx)


@login_required
def realized_summary_partial(request):
    """
    サマリー（全体＋ブローカー別）を部分描画して返す。
    """
    q = (request.GET.get("q") or "").strip()

    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    agg = _aggregate(qs)
    agg_brokers = _aggregate_by_broker(qs)  # ★ broker_label 付き

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
    - pnl:    各月の “投資家PnL”（= cashflow フィールド合計）
    - cash:   各月の “現金フロー”
              ＊現物/NISA: cashflow_calc（受け渡しベース）
              ＊信用      : pnl_display（手入力PnL）
    ついでにデバッグ用に cash_spec / cash_margin も返す。
    """
    q = (request.GET.get("q") or "").strip()

    qs = RealizedTrade.objects.filter(user=request.user)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    # cashflow_calc / pnl_display を注入
    qs = _with_metrics(qs)

    monthly = (
        qs.annotate(m=TruncMonth("trade_at"))
          .values("m")
          .annotate(
              # 投資家PnL（月次）
              pnl = Coalesce(
                  Sum("pnl_display", output_field=DEC2),
                  Value(Decimal("0"), output_field=DEC2)
              ),
              # 現物/NISA は実受渡（cashflow_calc）
              cash_spec = Coalesce(
                  Sum("cashflow_calc", filter=Q(account__in=["SPEC", "NISA"]), output_field=DEC2),
                  Value(Decimal("0"), output_field=DEC2)
              ),
              # 信用は手入力PnLを現金相当として扱う
              cash_margin = Coalesce(
                  Sum("pnl_display", filter=Q(account="MARGIN"), output_field=DEC2),
                  Value(Decimal("0"), output_field=DEC2)
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

    return JsonResponse({
        "labels": labels,
        "pnl": pnl,
        "pnl_cum": pnl_cum,        # 右軸の累積PnL
        "cash": cash,              # 棒グラフ用（現物=受渡, 信用=PnL）
        "cash_spec": cash_spec,    # 任意（デバッグ用）
        "cash_margin": cash_margin # 任意（デバッグ用）
    })

from decimal import Decimal


@login_required
@require_GET
def realized_ranking_partial(request):
    """
    銘柄別ランキング（期間連動）
    - 今月/指定期間で0件なら、自動で「直近12か月」にフォールバックして表示
    """
    q = (request.GET.get("q") or "").strip()
    # 期間（デフォルト: THIS_MONTH）
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
                  n   = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
                  qty = Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),
                  pnl = Coalesce(Sum("pnl_display", output_field=DEC2),
                                 Value(Decimal("0"), output_field=DEC2)),
                  wins = Coalesce(
                      Sum(Case(When(pnl_display__gt=0, then=1),
                               default=0, output_field=IntegerField())),
                      Value(0), output_field=IntegerField()
                  ),
              )
        )
        rows = []
        for r in grouped:
            n = int(r["n"] or 0)
            wins = int(r["wins"] or 0)
            rows.append({
                "ticker": r["ticker"],
                "name":   r["name"],
                "n":      n,
                "qty":    int(r["qty"] or 0),
                "pnl":    r["pnl"] or Decimal("0"),
                "avg":    (r["pnl"] / n) if n else Decimal("0"),
                "win_rate": (wins * 100.0 / n) if n else 0.0,
            })
        return rows

    # まずは指定期間（通常は今月）
    rows = build_rows(apply_period(base, start, end))
    used_preset = preset

    # 0件なら直近12か月にフォールバック
    if not rows:
        today = timezone.localdate()
        start_fb = (today.replace(day=1) - timezone.timedelta(days=365)).replace(day=1)
        end_fb = today
        rows = build_rows(apply_period(base, start_fb, end_fb))
        used_preset = "LAST_12M"

    # TOP/WORST
    top5   = sorted(rows, key=lambda x: (x["pnl"], x["win_rate"]), reverse=True)[:5]
    worst5 = sorted(rows, key=lambda x: (x["pnl"], -x["win_rate"]))[:5]

    ctx = {
        "top5": top5,
        "worst5": worst5,
        # テンプレ側のUI維持用
        "preset": used_preset, "freq": freq,
        "start": start, "end": end, "q": q,
    }
    return render(request, "realized/_ranking.html", ctx)


@login_required
@require_GET
def realized_ranking_detail_partial(request):
    """
    銘柄ドリルダウン（期間連動）
    GET: ticker, q, preset/freq/start/end
    返却: _ranking_detail.html
    """
    ticker = (request.GET.get("ticker") or "").strip()
    q = (request.GET.get("q") or "").strip()
    start, end, preset = _parse_period(request)

    if not ticker:
        return render(request, "realized/_ranking_detail.html",
                      {"ticker": "", "rows": [], "agg": {}})

    qs = RealizedTrade.objects.filter(user=request.user, ticker=ticker)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
    if start:
        qs = qs.filter(trade_at__gte=start)
    if end:
        qs = qs.filter(trade_at__lte=end)

    qs = _with_metrics(qs).order_by("-trade_at", "-id")

    # ここがポイント：dec0 は Value(...) で output_field を DEC2 に
    dec0 = Value(Decimal("0"), output_field=DEC2)

    agg = qs.aggregate(
        n   = Coalesce(Count("id"), Value(0), output_field=IntegerField()),
        qty = Coalesce(Sum("qty"), Value(0), output_field=IntegerField()),

        # 型混在を避けるため Sum/Avg にも output_field=DEC2 を明示
        pnl = Coalesce(
            Sum(Coalesce(F("pnl_display"), dec0), output_field=DEC2),
            dec0
        ),
        avg = Coalesce(
            Avg(Coalesce(F("pnl_display"), dec0), output_field=DEC2),
            dec0
        ),
        wins = Coalesce(
            Sum(Case(When(pnl_display__gt=0, then=1), default=0,
                     output_field=IntegerField())),
            Value(0), output_field=IntegerField()
        ),
    )

    n = agg.get("n") or 0
    wins = agg.get("wins") or 0
    agg["win_rate"] = (wins * 100.0 / n) if n else 0.0

    rows = list(qs[:5])  # 直近5件

    return render(request, "realized/_ranking_detail.html", {
        "ticker": ticker,
        "rows": rows,
        "agg": agg,
    })

# ============================================================
#  画面
# ============================================================

@login_required
@require_GET
def list_page(request):
    q = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_metrics(qs)
    agg  = _aggregate(qs)
    agg_brokers = _aggregate_by_broker(qs)

    return render(request, "realized/list.html", {
        "q": q,
        "trades": rows,
        "agg": agg,
        "agg_brokers": agg_brokers,
    })

# ============================================================
#  作成
#   - pnl_input を “手入力の実損（投資家PnL）” として cashflow に保存
#   - fee はそのまま保存（現金計算に利用）
# ============================================================
@login_required
@require_POST
def create(request):
    # --- 日付 ---
    date_raw = (request.POST.get("date") or "").strip()
    try:
        trade_at = (
            timezone.datetime.fromisoformat(date_raw).date()
            if date_raw else timezone.localdate()
        )
    except Exception:
        trade_at = timezone.localdate()

    # --- 主要入力 ---
    ticker  = (request.POST.get("ticker")  or "").strip()
    name    = (request.POST.get("name")    or "").strip()
    side    = (request.POST.get("side")    or "SELL").upper()
    broker  = (request.POST.get("broker")  or "OTHER").upper()
    account = (request.POST.get("account") or "SPEC").upper()

    try:
        qty = int(request.POST.get("qty") or 0)
    except Exception:
        qty = 0

    price      = _to_dec(request.POST.get("price"))
    fee        = _to_dec(request.POST.get("fee"))
    tax        = _to_dec(request.POST.get("tax"))        # 無ければ 0
    pnl_input  = _to_dec(request.POST.get("pnl_input"))  # “投資家PnL”（手入力の実損）
    memo       = (request.POST.get("memo") or "").strip()

    # --- バリデーション ---
    if not ticker or qty <= 0 or price <= 0:
        return JsonResponse({"ok": False, "error": "入力が不足しています"}, status=400)
    if side not in ("SELL", "BUY"):
        return JsonResponse({"ok": False, "error": "Sideが不正です"}, status=400)

    # --- basis の決定 ---
    # BUY は平均PnL%計算に使わないので None のままでも可。
    # SELL は平均PnL%のため逆算して保存する。
    basis = None
    if side == "SELL" and qty > 0:
        try:
            basis_calc = price - (pnl_input + fee + tax) / Decimal(qty)
            basis = basis_calc if basis_calc > 0 else None  # 不自然な値は除外
        except Exception:
            basis = None

    # --- 保有日数（任意）---
    # 1) 明示 `hold_days`（数値）を優先
    # 2) もしくは `opened_at`（YYYY-MM-DD）から trade_at との差で算出
    hold_days = None
    try:
        hd_raw = (request.POST.get("hold_days") or "").strip()
        if hd_raw != "":
            hold_days = max(int(hd_raw), 0)
        else:
            opened_raw = (request.POST.get("opened_at") or "").strip()
            if opened_raw:
                opened_date = timezone.datetime.fromisoformat(opened_raw).date()
                hold_days = max((trade_at - opened_date).days, 0)
    except Exception:
        hold_days = None

    # --- 登録 ---
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
        cashflow=pnl_input,   # 画面に出す“投資家PnL”
        basis=basis,          # SELL のときは逆算結果
        hold_days=hold_days,  # 任意保存（平均保有日数の計算に使う）
        memo=memo,
    )

    # --- 再描画 ---
    q  = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_metrics(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)
    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})

# ============================================================
#  削除（テーブル＋サマリーを同時更新して返す）
# ============================================================
@login_required
@require_POST
def delete(request, pk: int):
    RealizedTrade.objects.filter(pk=pk, user=request.user).delete()

    q = (request.POST.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    rows = _with_metrics(qs)
    agg  = _aggregate(qs)

    table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
    summary_html = render_to_string("realized/_summary.html", {"agg": agg},     request=request)
    return JsonResponse({"ok": True, "table": table_html, "summary": summary_html})

# ============================================================
#  CSV（両方を出力：現金ベースと手入力PnL）
# ============================================================
@login_required
@require_GET
def export_csv(request):
    q  = (request.GET.get("q") or "").strip()
    qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
    qs = _with_metrics(qs)

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="realized_trades.csv"'
    w = csv.writer(resp)
    w.writerow(["trade_at", "ticker", "name", "side", "qty", "price",
                "fee", "cashflow_calc(現金)", "pnl_display(実損)", "broker", "account", "memo"])
    for t in qs:
        w.writerow([
            t.trade_at, t.ticker, smart_str(getattr(t, "name", "") or ""),
            t.side, t.qty, t.price,
            t.fee,
            getattr(t, "cashflow_calc", Decimal("0.00")),
            getattr(t, "pnl_display",  Decimal("0.00")),
            smart_str(getattr(t, "broker", "") or ""),
            smart_str(getattr(t, "account", "") or ""),
            smart_str(t.memo or ""),
        ])
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
    明細テーブル
      - q:      フリーワード
      - ym:     'YYYY-MM'（これがあれば最優先で月フィルタ）
      - start/end: 'YYYY-MM-DD'（ym が無い場合の期間指定）
      - format: 'json' なら JSON で返す
    """
    try:
        q         = (request.GET.get("q") or "").strip()
        ym        = (request.GET.get("ym") or "").strip()
        start_s   = (request.GET.get("start") or "").strip()
        end_s     = (request.GET.get("end") or "").strip()
        want_json = (request.GET.get("format") or "").lower() == "json" \
                    or "application/json" in (request.headers.get("Accept") or "")

        qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")

        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

        # ---- 月フィルタ（最優先で確実に 1 ヶ月に絞る）----
        if ym and len(ym) == 7 and ym[4] == "-":
            y = int(ym[:4]); m = int(ym[5:7])
            # DateField/DateTimeField どちらでも確実に絞れるよう、日付帯で切る
            start_d = date(y, m, 1)
            # 月末日
            if m == 12:
                next_first = date(y + 1, 1, 1)
            else:
                next_first = date(y, m + 1, 1)

            # DateTimeField でも安全に。end は翌月 1 日 00:00 未満
            tz = timezone.get_current_timezone()
            start_dt = datetime.combine(start_d, time.min).replace(tzinfo=tz)
            end_dt   = datetime.combine(next_first, time.min).replace(tzinfo=tz)
            qs = qs.filter(trade_at__gte=start_dt, trade_at__lt=end_dt)

        else:
            # ---- 従来の start/end もサポート（指定がある場合のみ）----
            start_d = _parse_ymd(start_s)
            end_d   = _parse_ymd(end_s)
            if start_d and end_d:
                if end_d < start_d:
                    start_d, end_d = end_d, start_d
                tz = timezone.get_current_timezone()
                start_dt = datetime.combine(start_d, time.min).replace(tzinfo=tz)
                end_dt   = datetime.combine(end_d + timedelta(days=1), time.min).replace(tzinfo=tz)
                qs = qs.filter(trade_at__gte=start_dt, trade_at__lt=end_dt)

        rows = _with_metrics(qs)
        html = render_to_string("realized/_table.html", {"trades": rows}, request=request)

        if want_json:
            return JsonResponse({"ok": True, "html": html})
        return HttpResponse(html)

    except Exception as e:
        logger.exception("table_partial error: %s", e)
        tb = traceback.format_exc()
        err_html = f"""
        <div class="p-3 rounded-lg" style="background:#2b1f24;color:#ffd1d1;border:1px solid #ff9aa9;">
          <div style="font-weight:700;margin-bottom:6px">テーブル取得に失敗しました</div>
          <div style="margin-bottom:8px">{str(e)}</div>
          <details style="font-size:12px;opacity:.85">
            <summary>詳細</summary>
            <pre style="white-space:pre-wrap">{tb}</pre>
          </details>
        </div>
        """
        if (request.GET.get("format") or "").lower() == "json" \
           or "application/json" in (request.headers.get("Accept") or ""):
            return JsonResponse({"ok": False, "html": err_html, "error": str(e)})
        return HttpResponse(err_html)

    except Exception as e:
        logger.exception("table_partial error: %s", e)
        tb = traceback.format_exc()
        err_html = f"""
        <div class="p-3 rounded-lg" style="background:#2b1f24;color:#ffd1d1;border:1px solid #ff9aa9;">
          <div style="font-weight:700;margin-bottom:6px">テーブル取得に失敗しました</div>
          <div style="margin-bottom:8px">{str(e)}</div>
          <details style="font-size:12px;opacity:.85">
            <summary>詳細</summary>
            <pre style="white-space:pre-wrap">{tb}</pre>
          </details>
        </div>
        """
        if (request.GET.get("format") or "").lower() == "json" \
           or "application/json" in (request.headers.get("Accept") or ""):
            return JsonResponse({"ok": False, "html": err_html, "error": str(e)})
        return HttpResponse(err_html)

@login_required
@require_GET
def summary_partial(request):
    try:
        q  = (request.GET.get("q") or "").strip()
        qs = RealizedTrade.objects.filter(user=request.user).order_by("-trade_at", "-id")
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
        agg = _aggregate(qs)
        agg_brokers = _aggregate_by_broker(qs)  # ★ 追加
        return render(request, "realized/_summary.html", {"agg": agg, "agg_brokers": agg_brokers, "q": q})
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
    保有 → 売却のボトムシート。
    HTMX(hx-get) で #sheetRoot に innerHTML として差し込むため、
    ここは JSON ではなく “素のHTML” を返す。
    """
    try:
        # --- Holding 取得（user フィールド有無の両対応）---
        holding_filters = {"pk": pk}
        if any(f.name == "user" for f in Holding._meta.fields):
            holding_filters["user"] = request.user
        h = get_object_or_404(Holding, **holding_filters)

        # --- 直近 RealizedTrade（user フィールド有無の両対応）---
        rt_qs = RealizedTrade.objects.all()
        if any(f.name == "user" for f in RealizedTrade._meta.fields):
            rt_qs = rt_qs.filter(user=request.user)
        last = rt_qs.order_by("-trade_at", "-id").first()

        def g(obj, name, default=""):
            return getattr(obj, name, default) if obj is not None else default

        # quantity / qty 両対応（新 Holding は quantity 想定）
        h_qty = g(h, "quantity", None)
        if h_qty in (None, ""):
            h_qty = g(h, "qty", 0)

        # ★ プリセット：可能なら Holding の broker / account を優先
        pre_broker  = (g(h, "broker", "") or g(last, "broker", "") or "OTHER")
        pre_account = (g(h, "account", "") or g(last, "account", "") or "SPEC")

        ctx = {
            "h": h,
            "h_qty": h_qty,  # ← テンプレから常にこれを参照
            "prefill": {
                "date": timezone.localdate().isoformat(),
                "side": "SELL",
                "ticker": g(h, "ticker", ""),
                "name":   g(h, "name", ""),
                "qty":    h_qty,
                "price":  "",
                "fee":    g(last, "fee", 0),
                "cashflow": g(last, "cashflow", ""),
                "memo":   "",
                "broker": pre_broker,        # ← Holding 優先
                "account": pre_account,      # ← Holding 優先（SPEC/MARGIN/NISA）
            },
        }

        html = render_to_string("realized/_close_sheet.html", ctx, request=request)
        return HttpResponse(html)  # ★ HTML をそのまま返す

    except Exception as e:
        # 失敗時も 200 で “エラー用の簡易シートHTML” を返す（スマホで原因を見せる）
        logger.exception("close_sheet error (pk=%s): %s", pk, e)
        import traceback
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
    保有行の「売却」を登録（平均取得から手数料を逆算）。
    - 実損（手数料控除前）＝ cashflow（±で手入力）
    - 手数料 = (売値 − basis) × 数量 − 実損
    - Holding.user の有無、通常POST/HTMX の両方に耐える
    - ★ basis と hold_days を RealizedTrade に保存
    """
    try:
        # --- Holding 取得（user 有無の両対応） ---
        filters = {"pk": pk}
        if any(f.name == "user" for f in Holding._meta.fields):
            filters["user"] = request.user
        h = get_object_or_404(Holding, **filters)

        # --- 入力 ---
        date_raw = (request.POST.get("date") or "").strip()
        try:
            trade_at = (
                timezone.datetime.fromisoformat(date_raw).date()
                if date_raw else timezone.localdate()
            )
        except Exception:
            trade_at = timezone.localdate()

        side = "SELL"
        try:
            qty_in = int(request.POST.get("qty") or 0)
        except Exception:
            qty_in = 0

        price       = _to_dec(request.POST.get("price"))
        cashflow_in = request.POST.get("cashflow")  # 実損（手数料控除前 / ±）
        pnl_input   = None if cashflow_in in (None, "") else _to_dec(cashflow_in)

        broker  = (request.POST.get("broker")  or "OTHER").upper()
        account = (request.POST.get("account") or "SPEC").upper()
        memo    = (request.POST.get("memo")    or "").strip()
        name    = (request.POST.get("name")    or "").strip() or getattr(h, "name", "") or ""

        # --- バリデーション（数量/価格） ---
        held_qty = getattr(h, "quantity", None)
        if held_qty is None:
            held_qty = getattr(h, "qty", 0)
        if qty_in <= 0 or price <= 0 or qty_in > held_qty:
            return JsonResponse({"ok": False, "error": "数量/価格を確認してください"}, status=400)

        # --- basis(平均取得単価) 検出 ---
        basis_candidates = [
            "avg_cost", "average_cost", "avg_price", "average_price",
            "basis", "cost_price", "cost_per_share", "avg", "average",
            "avg_unit_cost", "avg_purchase_price",
        ]
        basis = None
        for fname in basis_candidates:
            v = getattr(h, fname, None)
            if v not in (None, ""):
                try:
                    basis = Decimal(str(v))
                    break
                except Exception:
                    continue
        if basis is None:
            return JsonResponse(
                {"ok": False, "error": "保有の平均取得単価(basis)が見つかりません。"},
                status=400,
            )

        # --- 実損が未入力なら 0 扱い ---
        if pnl_input is None:
            pnl_input = Decimal("0")

        # --- 手数料を逆算 ---
        fee = (price - basis) * Decimal(qty_in) - pnl_input

        # --- 保有日数（opened_at を優先。無ければ created_at を使用） ---
        days_held = None
        try:
            opened_date = None
            oa = getattr(h, "opened_at", None)
            if oa:
                opened_date = oa  # DateField

            ca = getattr(h, "created_at", None)
            if not opened_date and ca:
                opened_date = ca.date()  # DateTime -> date

            if opened_date:
                d = (trade_at - opened_date).days
                days_held = max(d, 0)
        except Exception:
            days_held = None

        # --- 登録 ---
        rt_kwargs = dict(
            trade_at=trade_at,
            side=side,
            ticker=getattr(h, "ticker", ""),
            name=name,
            broker=broker,
            account=account,
            qty=qty_in,
            price=price,
            fee=fee,
            cashflow=pnl_input,   # 実損（±）
            basis=basis,          # 平均取得単価
            hold_days=days_held,  # 保有日数
            memo=memo,
        )
        if any(f.name == "user" for f in RealizedTrade._meta.fields):
            rt_kwargs["user"] = request.user
        RealizedTrade.objects.create(**rt_kwargs)

        # --- 保有数量の更新（0 以下で削除）---
        if hasattr(h, "quantity"):
            h.quantity = F("quantity") - qty_in
            h.save(update_fields=["quantity"])
            h.refresh_from_db()
            if h.quantity <= 0:
                h.delete()
        else:
            # 旧フィールド名互換
            h.qty = F("qty") - qty_in
            h.save(update_fields=["qty"])
            h.refresh_from_db()
            if h.qty <= 0:
                h.delete()

        # --- 再描画片を用意 ---
        q = (request.POST.get("q") or "").strip()
        qs = RealizedTrade.objects.all()
        if any(f.name == "user" for f in RealizedTrade._meta.fields):
            qs = qs.filter(user=request.user)
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
        qs = qs.order_by("-trade_at", "-id")

        rows = _with_metrics(qs)
        agg  = _aggregate(qs)

        table_html   = render_to_string("realized/_table.html",   {"trades": rows}, request=request)
        summary_html = render_to_string("realized/_summary.html", {"agg": agg, "q": q}, request=request)

        # 保有一覧（user フィールド有無に対応）
        try:
            holdings_qs = Holding.objects.all()
            if any(f.name == "user" for f in Holding._meta.fields):
                holdings_qs = holdings_qs.filter(user=request.user)
            holdings_html = render_to_string(
                "holdings/_list.html", {"holdings": holdings_qs}, request=request
            )
        except Exception:
            holdings_html = ""

        # --- HTMX / 通常POST 両対応 ---
        if request.headers.get("HX-Request") == "true":
            return JsonResponse({"ok": True, "table": table_html, "summary": summary_html, "holdings": holdings_html})
        else:
            from django.shortcuts import redirect
            return redirect("realized_list")

    except Exception as e:
        import traceback
        if request.headers.get("HX-Request") == "true":
            return JsonResponse(
                {"ok": False, "error": str(e), "traceback": traceback.format_exc()},
                status=400,
            )
        from django.shortcuts import redirect
        return redirect("realized_list")