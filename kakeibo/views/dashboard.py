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
# 3) ★楽天評価額（rakuten_eval）を「楽天アプリの定義」に完全一致させる
#    → 現物：評価額を足す / 信用(MARGIN)：建玉“評価損益”だけを足す
# 4) テンプレは HTML/CSS/JS を分離（dashboard.html 参照）
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


def _portfolio_rakuten_available_from_cash_dashboard(today) -> int:
    """
    何をする？
    - cash_dashboard（現金ダッシュボード）と同じ定義で「楽天の余力（白丸）」を取る。
    - portfolio.services.cash_service.broker_summaries(today) の broker="楽天" の available を返す。
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


def _fx_usd_jpy() -> float:
    """
    何をする？
    - USD/JPY を取得して返す（失敗時は 1.0）
    - dashboard 用：軽量・堅牢に。yfinance が落ちたら 1.0 フォールバック。
    """
    try:
        import yfinance as yf
        df = yf.download("JPY=X", period="5d", interval="1d", auto_adjust=False, progress=False)
        if df is None or df.empty:
            return 1.0
        s = df["Close"].dropna()
        if s.empty:
            return 1.0
        return float(s.iloc[-1])
    except Exception:
        return 1.0


def _norm_ticker_for_yf(ticker: str) -> str:
    """
    何をする？
    - portfolio 側の normalize を使って yfinance 用のシンボルに揃える
    """
    try:
        from portfolio.services import trend as svc_trend
        return svc_trend._normalize_ticker(str(ticker or ""))
    except Exception:
        return str(ticker or "")


def _last_close_map(tickers_norm: list[str]) -> dict[str, float]:
    """
    何をする？
    - 複数ティッカーの終値をまとめて取って dict で返す（取れないものは欠ける）
    """
    out: dict[str, float] = {}
    if not tickers_norm:
        return out

    try:
        import yfinance as yf
        import pandas as pd

        df = yf.download(
            tickers=tickers_norm if len(tickers_norm) > 1 else tickers_norm[0],
            period="10d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
        if df is None:
            return out

        def _pick(nsym: str) -> float | None:
            try:
                if hasattr(df, "columns") and isinstance(df.columns, pd.MultiIndex):
                    if (nsym, "Close") in df.columns:
                        s = df[(nsym, "Close")]
                    else:
                        s = df.xs(nsym, axis=1)["Close"]
                else:
                    # 1銘柄のとき
                    s = df["Close"]
                s = pd.Series(s).dropna()
                if s.empty:
                    return None
                return float(s.iloc[-1])
            except Exception:
                return None

        for n in tickers_norm:
            v = _pick(n)
            if v is not None:
                out[n] = v

        return out
    except Exception:
        return out


def _portfolio_rakuten_eval_like_rakuten(user) -> int:
    """
    何をする？
    - 楽天アプリの「評価額合計（あなたのスクショの 4,219,034 の定義）」に合わせる。

    ルール：
    - 現物（NISA/特定など）：評価額（時価評価額）を足す
    - 信用（account="MARGIN"）：建玉“評価損益（含み損益）”だけを足す
      ※ 信用建玉の「評価額」は足さない（ここがズレの原因）

    価格ソース：
    - yfinance 終値（holdings と同系統）を使用
    """
    try:
        from portfolio.models import Holding
    except Exception:
        return 0

    qs = Holding.objects.filter(user=user, broker="RAKUTEN").order_by("id")
    holds = list(qs)
    if not holds:
        return 0

    # 価格をまとめて取る（終値）
    norms = [_norm_ticker_for_yf(h.ticker) for h in holds]
    price_map = _last_close_map(list(dict.fromkeys(norms)))  # 重複排除

    usd_jpy = _fx_usd_jpy()

    total = Decimal("0")

    for h in holds:
        try:
            q = int(h.quantity or 0)
            if q <= 0:
                continue

            cur = (getattr(h, "currency", "JPY") or "JPY").upper()
            fx = Decimal(str(usd_jpy if cur == "USD" else 1.0))

            n = _norm_ticker_for_yf(h.ticker)
            price = price_map.get(n)
            if price is None:
                continue
            price = Decimal(str(price))

            # 評価額（建玉の時価）
            valuation_jpy = price * fx * Decimal(q)

            # 含み損益（BUY/SELL対応）
            cost_unit = Decimal(str(h.avg_cost or 0))
            side = (getattr(h, "side", "BUY") or "BUY").upper()
            if side == "SELL":
                pnl_jpy = (cost_unit * fx - price * fx) * Decimal(q)
            else:
                pnl_jpy = (price * fx - cost_unit * fx) * Decimal(q)

            acc = (getattr(h, "account", "") or "").upper()

            if acc == "MARGIN":
                # ★楽天の「信用建玉評価損益」扱い：評価損益だけ足す
                total += pnl_jpy
            else:
                # ★現物：評価額を足す
                total += valuation_jpy

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

    # ★修正：固定費は当月分だけにする
    year_fixed = _int(fixed_sum)
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

    # ★ここが今回の本丸：楽天アプリと同じ定義の「評価額」
    rakuten_eval = _portfolio_rakuten_eval_like_rakuten(request.user)

    invest_total = _int(rakuten_cash_free + rakuten_eval)

    # -----------------------
    # KPI
    # -----------------------
    total_assets = _int(rakuten_bank_b + aeon_bank_house + invest_total)

    # あなたのルール：実残高 = 4,136,736 - 余力 - 楽天銀行(B)
    rakuten_bank_actual = _int(4_136_736 - rakuten_cash_free - rakuten_bank_b)

    # -----------------------
    # 可視化用（%）
    # -----------------------
    expense_rate = _pct(total_expense, total_income)
    fixed_rate = _pct(fixed_sum, total_expense)
    var_rate = _pct(var_sum, total_expense)

    context = {
        "title": "家計簿",
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