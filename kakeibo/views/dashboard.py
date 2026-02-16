# =========================================
# [FILE] dashboard.py
# [PATH] kakeibo/views/dashboard.py
#
# 家計簿トップ画面（/kakeibo/）。
# ★修正点：
#   ・楽天評価額を holdings と同じロジックで算出
#     - userで絞る
#     - yfinance終値使用
#     - USD→JPY換算
# =========================================

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from .permissions import kakeibo_access_required
from ..models import MonthlyIncome, FixedExpenseTemplate, MonthlyVariableExpense, BankBalance, Account


def month_first(d):
    return d.replace(day=1)


def add_month(d, delta: int):
    y = d.year
    m = d.month + delta
    while m <= 0:
        y -= 1
        m += 12
    while m >= 13:
        y += 1
        m -= 12
    return d.replace(year=y, month=m, day=1)


def _int(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def _sum_qs(qs, field: str):
    return _int(qs.aggregate(s=Sum(field))["s"])


def _pct(num: int, den: int):
    den = _int(den)
    num = _int(num)
    if den <= 0:
        return None
    try:
        return round((num / den) * 100.0, 1)
    except Exception:
        return None


def _delta(a: int, b: int) -> int:
    return _int(a) - _int(b)


def _delta_pct(now: int, prev: int):
    prev = _int(prev)
    now = _int(now)
    if prev == 0:
        return None
    try:
        return round(((now - prev) / prev) * 100.0, 1)
    except Exception:
        return None


# -----------------------------
# 銀行残高フォールバック
# -----------------------------
def _bank_balance_fallback(month, owner: str, name_contains: str) -> dict:
    accs = (
        Account.objects
        .filter(kind="ACCOUNT", owner=owner, name__icontains=name_contains)
        .order_by("id")
    )
    if not accs.exists():
        return {"balance": 0, "month": None, "account_name": None}

    obj = (
        BankBalance.objects
        .filter(account__in=accs, month__lte=month)
        .select_related("account")
        .order_by("-month", "-id")
        .first()
    )
    if not obj:
        return {"balance": 0, "month": None, "account_name": accs.first().name}

    mm = getattr(obj, "month", None)
    month_label = f"{mm.year}-{mm.month:02d}" if mm else None
    return {
        "balance": _int(getattr(obj, "balance", 0)),
        "month": month_label,
        "account_name": getattr(getattr(obj, "account", None), "name", None),
    }


# -----------------------------
# 楽天余力（白丸）
# -----------------------------
def _portfolio_rakuten_available_from_cash_dashboard(today) -> int:
    try:
        from portfolio.services import cash_service as cash_svc
    except Exception:
        return 0

    try:
        rows = cash_svc.broker_summaries(today) or []
    except Exception:
        return 0

    for r in rows:
        if (r.get("broker") or "").strip() == "楽天":
            return _int(r.get("available", 0))

    return 0


# -----------------------------
# ★ 楽天評価額（holdings完全一致版）
# -----------------------------
def _portfolio_rakuten_eval(user) -> int:
    try:
        import yfinance as yf
        import pandas as pd
        from portfolio.models import Holding
        from portfolio.services import trend as svc_trend
    except Exception:
        return 0

    qs = Holding.objects.filter(user=user, broker="RAKUTEN")
    holdings = list(qs)
    if not holdings:
        return 0

    def _norm(raw):
        return svc_trend._normalize_ticker(str(raw or ""))

    tickers = [_norm(h.ticker) for h in holdings if h.ticker]

    if not tickers:
        return 0

    try:
        df = yf.download(
            tickers if len(tickers) > 1 else tickers[0],
            period="40d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception:
        return 0

    def _last_close(sym):
        try:
            if isinstance(df.columns, pd.MultiIndex):
                s = df[(sym, "Close")]
            else:
                s = df["Close"]
            s = pd.Series(s).dropna()
            if s.empty:
                return None
            return float(s.iloc[-1])
        except Exception:
            return None

    def _usd_to_jpy():
        try:
            fx = yf.download("JPY=X", period="5d", interval="1d", progress=False)
            if fx is None or fx.empty:
                return 1.0
            return float(fx["Close"].dropna().iloc[-1])
        except Exception:
            return 1.0

    usd_rate = _usd_to_jpy()

    total = 0.0
    for h in holdings:
        sym = _norm(h.ticker)
        last = _last_close(sym)
        if last is None:
            continue

        qty = _int(h.quantity)
        cur = (h.currency or "JPY").upper()
        fx = usd_rate if cur == "USD" else 1.0

        total += last * fx * qty

    return _int(total)


# =================================================
#                     VIEW
# =================================================
@login_required
def dashboard(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("No access.")

    today = timezone.localdate()
    m = month_first(today)
    prev_m = add_month(m, -1)

    debug = (request.GET.get("debug") or "").strip() == "1"

    # -----------------------
    # 当月
    # -----------------------
    total_income = _sum_qs(MonthlyIncome.objects.filter(month=m), "amount")
    fixed_sum = _sum_qs(FixedExpenseTemplate.objects.filter(is_active=True), "amount")
    var_sum = _sum_qs(MonthlyVariableExpense.objects.filter(month=m), "amount")

    total_expense = _int(fixed_sum + var_sum)
    month_diff = _int(total_income - total_expense)

    # -----------------------
    # 先月
    # -----------------------
    prev_income = _sum_qs(MonthlyIncome.objects.filter(month=prev_m), "amount")
    prev_var = _sum_qs(MonthlyVariableExpense.objects.filter(month=prev_m), "amount")
    prev_fixed = fixed_sum
    prev_expense = _int(prev_fixed + prev_var)

    d_income = _delta(total_income, prev_income)
    d_expense = _delta(total_expense, prev_expense)
    d_fixed = _delta(fixed_sum, prev_fixed)
    d_var = _delta(var_sum, prev_var)

    dp_income = _delta_pct(total_income, prev_income)
    dp_expense = _delta_pct(total_expense, prev_expense)
    dp_fixed = _delta_pct(fixed_sum, prev_fixed)
    dp_var = _delta_pct(var_sum, prev_var)

    # -----------------------
    # 年度
    # -----------------------
    y = m.year
    year_income = _int(MonthlyIncome.objects.filter(month__year=y).aggregate(s=Sum("amount"))["s"] or 0)
    year_var = _int(MonthlyVariableExpense.objects.filter(month__year=y).aggregate(s=Sum("amount"))["s"] or 0)

    year_fixed = _int(fixed_sum * 12)
    year_expense = _int(year_fixed + year_var)
    year_diff = _int(year_income - year_expense)

    # -----------------------
    # 銀行
    # -----------------------
    rakuten_bank = _bank_balance_fallback(m, "B", "楽天銀行")
    aeon_bank = _bank_balance_fallback(m, "HOUSE", "イオン銀行")

    rakuten_bank_b = _int(rakuten_bank["balance"])
    aeon_bank_house = _int(aeon_bank["balance"])

    # -----------------------
    # 投資
    # -----------------------
    rakuten_cash_free = _portfolio_rakuten_available_from_cash_dashboard(today)
    rakuten_eval = _portfolio_rakuten_eval(request.user)
    invest_total = _int(rakuten_cash_free + rakuten_eval)

    # -----------------------
    # KPI
    # -----------------------
    total_assets = _int(rakuten_bank_b + aeon_bank_house + invest_total)
    rakuten_bank_actual = _int(4_136_736 - rakuten_cash_free - rakuten_bank_b)

    expense_rate = _pct(total_expense, total_income)
    fixed_rate = _pct(fixed_sum, total_expense)
    var_rate = _pct(var_sum, total_expense)

    context = {
        "debug": debug,
        "month_label": f"{m.year}-{m.month:02d}",
        "prev_month_label": f"{prev_m.year}-{prev_m.month:02d}",

        "kpi_total_assets": total_assets,
        "kpi_rakuten_bank_actual": rakuten_bank_actual,
        "kpi_invest_total": invest_total,

        "rakuten_cash_free": rakuten_cash_free,
        "rakuten_eval": rakuten_eval,
        "rakuten_bank_b": rakuten_bank_b,
        "aeon_bank_house": aeon_bank_house,

        "rakuten_bank_month_used": rakuten_bank.get("month"),
        "rakuten_bank_account_name_used": rakuten_bank.get("account_name"),
        "aeon_bank_month_used": aeon_bank.get("month"),
        "aeon_bank_account_name_used": aeon_bank.get("account_name"),

        "year_label": f"{y}",
        "year_income": year_income,
        "year_expense": year_expense,
        "year_diff": year_diff,

        "total_income": total_income,
        "total_expense": total_expense,
        "month_diff": month_diff,
        "fixed_sum": fixed_sum,
        "var_sum": var_sum,

        "d_income": d_income,
        "d_expense": d_expense,
        "d_fixed": d_fixed,
        "d_var": d_var,
        "dp_income": dp_income,
        "dp_expense": dp_expense,
        "dp_fixed": dp_fixed,
        "dp_var": dp_var,

        "expense_rate": expense_rate,
        "fixed_rate": fixed_rate,
        "var_rate": var_rate,
    }

    return render(request, "kakeibo/dashboard.html", context)