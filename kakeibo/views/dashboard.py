# =========================================
# [FILE] dashboard.py
# [PATH] kakeibo/views/dashboard.py
#
# このファイルは何？
# 家計簿トップ画面（/kakeibo/）。
# - KPI：総資産 / 楽天銀行(B)の実残高 / 投資（評価額＋現金余力）
# - 年度（1月〜12月）の収支
# - 当月の収入・支出・差額＋固定/変動内訳
# - 先月比（収入/支出/固定/変動）
# - 可視化（支出率バー、固定/変動比バー）
#
# ★今回の修正ポイント
# 1) BankBalance（月次）が当月に無いと 0 になる問題
#    → month<=当月 の最新を拾う（フォールバック）
# 2) 楽天余力（白丸）は cash_dashboard の定義と揃える
#    → portfolio.services.cash_service.broker_summaries(today) の
#       broker="楽天" の available を使用（完全一致）
# 3) テンプレは HTML/CSS/JS を分離（dashboard.html 参照）
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


def _bank_balance_fallback(month, owner: str, name_contains: str) -> dict:
    """
    何をする？
    - 家計簿の銀行残高(BankBalance)を「当月が無ければ最新月」で拾う。
    - owner（HOUSE/B/G）と、口座名の部分一致で “口座” を探す。
    - その口座の BankBalance を month<=対象月 の範囲で最新を拾う。

    戻り値：
      {
        "balance": int,
        "month": "YYYY-MM" or None,
        "account_name": str or None,
      }
    """
    # まず口座候補（Account）を拾う
    accs = (
        Account.objects
        .filter(kind="ACCOUNT", owner=owner, name__icontains=name_contains)
        .order_by("id")
    )
    if not accs.exists():
        return {"balance": 0, "month": None, "account_name": None}

    # 次に BankBalance を「月次の最新」で拾う（当月が無くてもOK）
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


def _portfolio_rakuten_available_from_cash_dashboard(today) -> int:
    """
    何をする？
    - cash_dashboard（現金ダッシュボード）と同じ定義で「楽天の余力（白丸）」を取る。
    - portfolio.services.cash_service.broker_summaries(today) の broker="楽天" の available を返す。

    これで「白丸とホームがズレる」問題を根絶できる。
    """
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


def _portfolio_rakuten_eval() -> int:
    """
    何をする？
    - 楽天証券（broker="RAKUTEN"）の保有評価額（時価総額）を合算して返す。
    - last_price が無いものは0扱い。
    """
    try:
        from portfolio.models import Holding
    except Exception:
        return 0

    total = Decimal("0")
    qs = Holding.objects.filter(broker="RAKUTEN")

    for h in qs:
        try:
            qty = int(h.quantity or 0)
            lp = h.last_price
            if lp is None:
                continue
            total += Decimal(qty) * Decimal(lp)
        except Exception:
            continue

    return _int(total)


@login_required
def dashboard(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    today = timezone.localdate()
    m = month_first(today)
    prev_m = add_month(m, -1)

    debug = (request.GET.get("debug") or "").strip() == "1"

    # -----------------------
    # kakeibo：当月
    # -----------------------
    total_income = _sum_qs(MonthlyIncome.objects.filter(month=m), "amount")

    fixed_sum = _sum_qs(
        FixedExpenseTemplate.objects.filter(is_active=True),
        "amount"
    )

    var_sum = _sum_qs(
        MonthlyVariableExpense.objects.filter(month=m),
        "amount"
    )

    total_expense = _int(fixed_sum + var_sum)
    month_diff = _int(total_income - total_expense)

    # -----------------------
    # kakeibo：先月
    # -----------------------
    prev_income = _sum_qs(MonthlyIncome.objects.filter(month=prev_m), "amount")

    prev_var = _sum_qs(
        MonthlyVariableExpense.objects.filter(month=prev_m),
        "amount"
    )

    # 固定費テンプレは「毎月同じ扱い」なので先月も同じ fixed_sum
    prev_fixed = fixed_sum
    prev_expense = _int(prev_fixed + prev_var)

    # 先月比（差分/率）
    d_income = _delta(total_income, prev_income)
    d_expense = _delta(total_expense, prev_expense)
    d_fixed = _delta(fixed_sum, prev_fixed)
    d_var = _delta(var_sum, prev_var)

    dp_income = _delta_pct(total_income, prev_income)
    dp_expense = _delta_pct(total_expense, prev_expense)
    dp_fixed = _delta_pct(fixed_sum, prev_fixed)
    dp_var = _delta_pct(var_sum, prev_var)

    # -----------------------
    # 年度（1月〜12月）
    # -----------------------
    y = m.year
    year_income = _int(
        MonthlyIncome.objects
        .filter(month__year=y)
        .aggregate(s=Sum("amount"))["s"] or 0
    )

    year_var = _int(
        MonthlyVariableExpense.objects
        .filter(month__year=y)
        .aggregate(s=Sum("amount"))["s"] or 0
    )

    # 固定費は「月次のテンプレ合計 × 12」で年度推定
    year_fixed = _int(fixed_sum * 12)
    year_expense = _int(year_fixed + year_var)
    year_diff = _int(year_income - year_expense)

    # -----------------------
    # 銀行（家計簿）：当月が無ければ最新月で拾う
    # -----------------------
    rakuten_bank = _bank_balance_fallback(m, owner="B", name_contains="楽天銀行")
    aeon_bank = _bank_balance_fallback(m, owner="HOUSE", name_contains="イオン銀行")

    rakuten_bank_b = _int(rakuten_bank["balance"])
    aeon_bank_house = _int(aeon_bank["balance"])

    # -----------------------
    # portfolio（楽天：投資）
    # -----------------------
    rakuten_cash_free = _portfolio_rakuten_available_from_cash_dashboard(today)  # 白丸と完全一致
    rakuten_eval = _portfolio_rakuten_eval()
    invest_total = _int(rakuten_cash_free + rakuten_eval)

    # -----------------------
    # KPI
    # -----------------------
    # あなたの定義：総資産 = 楽天銀行(B) + イオン銀行(家) + 楽天投資(評価額+余力)
    total_assets = _int(rakuten_bank_b + aeon_bank_house + invest_total)

    # あなたのルール：実残高 = 4,136,736 - 余力 - 楽天銀行(B)
    rakuten_bank_actual = _int(4_136_736 - rakuten_cash_free - rakuten_bank_b)

    # -----------------------
    # 可視化用（%）
    # -----------------------
    expense_rate = _pct(total_expense, total_income)       # 支出/収入
    fixed_rate = _pct(fixed_sum, total_expense)            # 固定/支出
    var_rate = _pct(var_sum, total_expense)                # 変動/支出

    context = {
        "title": "家計簿",
        "debug": debug,

        # month labels
        "month_label": f"{m.year}-{m.month:02d}",
        "prev_month_label": f"{prev_m.year}-{prev_m.month:02d}",

        # KPI
        "kpi_total_assets": total_assets,
        "kpi_rakuten_bank_actual": rakuten_bank_actual,
        "kpi_invest_total": invest_total,

        # KPI 補助（デバッグ表示用にも使う）
        "rakuten_cash_free": rakuten_cash_free,
        "rakuten_eval": rakuten_eval,
        "rakuten_bank_b": rakuten_bank_b,
        "aeon_bank_house": aeon_bank_house,

        # 銀行：どの月を拾ったか（当月が無いとズレるので、今だけ見える化）
        "rakuten_bank_month_used": rakuten_bank.get("month"),
        "rakuten_bank_account_name_used": rakuten_bank.get("account_name"),
        "aeon_bank_month_used": aeon_bank.get("month"),
        "aeon_bank_account_name_used": aeon_bank.get("account_name"),

        # 年度
        "year_label": f"{y}",
        "year_income": year_income,
        "year_expense": year_expense,
        "year_diff": year_diff,

        # 当月
        "total_income": total_income,
        "total_expense": total_expense,
        "month_diff": month_diff,
        "fixed_sum": fixed_sum,
        "var_sum": var_sum,

        # 先月比（差分 / 率）
        "d_income": d_income,
        "d_expense": d_expense,
        "d_fixed": d_fixed,
        "d_var": d_var,
        "dp_income": dp_income,
        "dp_expense": dp_expense,
        "dp_fixed": dp_fixed,
        "dp_var": dp_var,

        # 可視化
        "expense_rate": expense_rate,   # %
        "fixed_rate": fixed_rate,       # %
        "var_rate": var_rate,           # %
    }
    return render(request, "kakeibo/dashboard.html", context)