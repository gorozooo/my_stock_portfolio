# =========================================
# [FILE] dashboard.py
# [PATH] kakeibo/views/dashboard.py
#
# このファイルは何？
# 家計簿トップ画面（/kakeibo/）のビュー。
# - KPI：総資産 / 家の貯蓄 / 投資（評価額＋現金余力）
# - 年度（選択可）の収支
# - 月（選択可）の収入・支出・差額＋固定/変動内訳 + 先月比
# - 銀行残高：登録済み口座を全部（HOUSE / B / G）で表示
#   - 当月が無い口座は「最新月（選択月以前）」で表示
#   - Bの楽天銀行はトップで見えてるため、銀行残高セクションから除外
#
# 資金移動（表示用の計算）
# ① 千葉銀行（給与口座）：ローン後残高から「ATMで引き落とせる千円単位の上限」まで引き出す
#    - withdrawable = floor(balance/1000)*1000
#    - remain = balance - withdrawable
# ② 三井住友銀行(B)：Bのお小遣い＋立替（カード請求は考慮しない）
#    - need_b = 固定費お小遣い(B) + 変動ADVANCE(B) + 固定(立替)(B)
# ③ 楽天銀行(G)：Gのお小遣い＋立替−楽天カード(G)請求（確定分のみ）
#    - need_g = okodukai_g（現行の _calc_okodukai がその式）
# ④ イオン銀行(HOUSE)：最低残高をカード請求＋年金・保険として置き、差分を出す
#    - required_min = エポス(HOUSE) + イオンカード(HOUSE) + ヨドバシ(HOUSE) + 年金・保険
#    - delta = required_min - balance  （+なら不足 / -なら余剰）
#
# 今回の修正：
# - 家の貯蓄を正式計算へ変更
#   2025年12月終了時点の固定起点 -623,573
#   + 2026年1月から選択月までの月次差額累計
# - snapshot に古い kpi_rakuten_bank_actual が残っていても、表示では必ず正式計算を使う
# - 先月比も正式計算で比較する
# - 我が家の総資産も、snapshot の古い値に引っ張られないように
#   楽天銀行(B) + イオン銀行(家) + 楽天評価額 で表示用に再計算する
# - HOUSE のカード請求集計で、memo/category だけでなく card フィールド側の名前も見る
# =========================================

from decimal import Decimal
from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum, Q
from django.shortcuts import render, redirect
from django.utils import timezone

from .permissions import kakeibo_access_required
from ..models import (
    MonthlyIncome,
    FixedExpenseTemplate,
    MonthlyVariableExpense,
    BankBalance,
    Account,
    MonthlySnapshot,
    MonthlyDashboardMemo,
    MonthlyTodo,
)


# =========================================
# 家の貯蓄：正式な固定起点
# =========================================
HOUSE_SAVINGS_BASE_MONTH = date(2025, 12, 1)
HOUSE_SAVINGS_BASE_VALUE = -623_573


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


def _parse_month_yyyy_mm(s: str | None) -> date | None:
    """
    何をする？
    - "YYYY-MM" を date(YYYY, MM, 1) にする
    """
    if not s:
        return None
    s = str(s).strip()
    try:
        y, m = s.split("-")
        y = int(y)
        m = int(m)
        if y < 1900 or y > 2500:
            return None
        if m < 1 or m > 12:
            return None
        return date(y, m, 1)
    except Exception:
        return None


def _bank_balance_fallback(month, owner: str, name_contains: str) -> dict:
    """
    何をする？
    - 家計簿の銀行残高(BankBalance)を「当月が無ければ最新月」で拾う。
    - 主にKPI用（楽天銀行B / イオン家）で利用。
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


def _account_balance_fallback_for_account(month: date, acc: Account) -> dict:
    """
    何をする？
    - 特定の口座(Account)について、選択月が無ければ選択月以前の最新月で BankBalance を拾う
    """
    try:
        bb = (
            BankBalance.objects
            .filter(account=acc, month__lte=month)
            .order_by("-month", "-id")
            .first()
        )
        if not bb:
            return {"balance": 0, "month": None, "account_name": (acc.name or "").strip()}

        mm = getattr(bb, "month", None)
        month_label = f"{mm.year}-{mm.month:02d}" if mm else None
        return {
            "balance": _int(getattr(bb, "balance", 0)),
            "month": month_label,
            "account_name": (acc.name or "").strip(),
        }
    except Exception:
        return {"balance": 0, "month": None, "account_name": (acc.name or "").strip()}


def _owner_label(owner: str) -> str:
    o = (owner or "").strip().upper()
    if o == "HOUSE":
        return "家"
    if o == "B":
        return "ぼーや"
    if o == "G":
        return "ごろ"
    return o or "—"


def _bank_groups_all_accounts(month: date) -> tuple[dict, dict]:
    """
    何をする？
    - 銀行残高セクション用に、登録済み口座を owner(HOUSE/B/G) ごとにまとめる。
    - 各口座は「選択月が無ければ、選択月以前の最新月」で残高を表示。
    - Bの楽天銀行はトップで見えてるため、このリストから除外する（name に '楽天銀行' を含む）。
    """
    owners = ["HOUSE", "B", "G"]

    acc_qs = (
        Account.objects
        .filter(kind="ACCOUNT", owner__in=owners)
        .order_by("owner", "id")
    )

    bank_groups: dict[str, list[dict]] = {"HOUSE": [], "B": [], "G": []}
    bank_totals: dict[str, int] = {"HOUSE": 0, "B": 0, "G": 0}

    for acc in acc_qs:
        try:
            owner = (acc.owner or "").strip()
            name = (acc.name or "").strip()

            if owner == "B" and "楽天銀行" in name:
                continue

            bb = (
                BankBalance.objects
                .filter(account=acc, month__lte=month)
                .order_by("-month", "-id")
                .first()
            )

            if bb:
                bal = _int(getattr(bb, "balance", 0))
                mm = getattr(bb, "month", None)
                mm_label = f"{mm.year}-{mm.month:02d}" if mm else None
            else:
                bal = 0
                mm_label = None

            row = {"name": name, "balance": bal, "month": mm_label}

            if owner not in bank_groups:
                continue

            bank_groups[owner].append(row)
            bank_totals[owner] = _int(bank_totals.get(owner, 0) + bal)

        except Exception:
            continue

    return bank_groups, bank_totals


def _portfolio_rakuten_available_from_cash_dashboard(today) -> int:
    """
    何をする？
    - cash_dashboard と同じ定義で「楽天の余力（白丸）」を取る。
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
    - 複数ティッカーの終値をまとめて取って dict で返す
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
    - 楽天アプリの「評価額合計」に合わせる。
    - 現物：評価額を足す / 信用(MARGIN)：評価損益だけ足す
    """
    try:
        from portfolio.models import Holding
    except Exception:
        return 0

    qs = Holding.objects.filter(user=user, broker="RAKUTEN").order_by("id")
    holds = list(qs)
    if not holds:
        return 0

    norms = [_norm_ticker_for_yf(h.ticker) for h in holds]
    price_map = _last_close_map(list(dict.fromkeys(norms)))

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

            valuation_jpy = price * fx * Decimal(q)

            cost_unit = Decimal(str(h.avg_cost or 0))
            side = (getattr(h, "side", "BUY") or "BUY").upper()
            if side == "SELL":
                pnl_jpy = (cost_unit * fx - price * fx) * Decimal(q)
            else:
                pnl_jpy = (price * fx - cost_unit * fx) * Decimal(q)

            acc = (getattr(h, "account", "") or "").upper()

            if acc == "MARGIN":
                total += pnl_jpy
            else:
                total += valuation_jpy

        except Exception:
            continue

    return _int(total)


def _year_options() -> list[int]:
    """
    何をする？
    - DBに存在する月次データ（収入 or 変動費 or snapshot）から年度候補を作る
    """
    ys = set()
    for y in MonthlyIncome.objects.values_list("month__year", flat=True).distinct():
        if y:
            ys.add(int(y))
    for y in MonthlyVariableExpense.objects.values_list("month__year", flat=True).distinct():
        if y:
            ys.add(int(y))
    for y in MonthlySnapshot.objects.values_list("month__year", flat=True).distinct():
        if y:
            ys.add(int(y))
    for y in MonthlyDashboardMemo.objects.values_list("month__year", flat=True).distinct():
        if y:
            ys.add(int(y))
    for y in MonthlyTodo.objects.values_list("month__year", flat=True).distinct():
        if y:
            ys.add(int(y))
    if not ys:
        ys.add(timezone.localdate().year)
    return sorted(list(ys), reverse=True)


def _month_options(today: date, months_back: int = 24) -> list[dict]:
    """
    何をする？
    - 今日から遡って months_back ヶ月分の "YYYY-MM" リストを作る（UI用）
    """
    out = []
    m = month_first(today)
    for i in range(months_back):
        mm = add_month(m, -i)
        s = f"{mm.year}-{mm.month:02d}"
        out.append({"value": s, "label": s})
    return out


def _var_sum_for_expense(month: date) -> int:
    """
    何をする？
    - 支出に含める「変動費合計」を返す
    - ルール：B/G の CARD は除外（登録はするが支出に含めない）
             HOUSE の CARD は含める
             ADVANCE は含める
    """
    qs = MonthlyVariableExpense.objects.filter(month=month)
    qs = qs.exclude(var_type="CARD", owner__in=["B", "G"])
    return _sum_qs(qs, "amount")


def _card_bill_sum(month: date, owner: str) -> int:
    """
    何をする？
    - owner(B/G/HOUSE) の「カード合計請求額」を返す
    """
    qs = MonthlyVariableExpense.objects.filter(month=month, owner=owner, var_type="CARD")
    return _sum_qs(qs, "amount")


def _advance_var_sum(month: date, owner: str) -> int:
    """
    何をする？
    - owner(B/G) の「変動費：立替（ADVANCE）」合計
    """
    qs = MonthlyVariableExpense.objects.filter(month=month, owner=owner, var_type="ADVANCE")
    return _sum_qs(qs, "amount")


def _fixed_sum_category_contains(owner: str, contains: str) -> int:
    """
    何をする？
    - 固定費テンプレから、category名に contains を含むものを合計
    """
    qs = FixedExpenseTemplate.objects.filter(is_active=True, owner=owner, category__name__icontains=contains)
    return _sum_qs(qs, "amount")


def _fixed_sum_memo_contains(owner: str, contains: str) -> int:
    """
    何をする？
    - 固定費テンプレから、memo に contains を含むものを合計
    """
    qs = FixedExpenseTemplate.objects.filter(is_active=True, owner=owner, memo__icontains=contains)
    return _sum_qs(qs, "amount")


def _allowance_fixed_sum(owner: str) -> int:
    """
    何をする？
    - 「固定費で登録したそれぞれのお小遣い」合計
    """
    s = _fixed_sum_category_contains(owner, "お小遣い")
    if s:
        return s
    return _fixed_sum_memo_contains(owner, "お小遣い")


def _advance_fixed_sum(owner: str) -> int:
    """
    何をする？
    - 固定費側の「立替」合計
    """
    s = _fixed_sum_category_contains(owner, "立替")
    if s:
        return s
    return _fixed_sum_memo_contains(owner, "立替")


def _calc_okodukai(month: date, owner: str) -> int:
    """
    何をする？
    - お小遣い（自動計算）
      （固定費お小遣い）＋（立替合計）−（カード請求合計）
    """
    fixed_allow = _allowance_fixed_sum(owner)
    adv_total = _int(_advance_var_sum(month, owner) + _advance_fixed_sum(owner))
    bill = _card_bill_sum(month, owner)
    return _int(fixed_allow + adv_total - bill)


def _house_card_bill_sum_by_keyword(month: date, keyword: str) -> int:
    """
    何をする？
    - HOUSE のカード請求（var_type=CARD）から、keyword（例：エポス）に一致する分を合計
    - memo / category.name / card.name / card文字列 のどれでも拾う
    - ORMのjoin依存を避けるため、Python側で安全に判定する
    """
    kw = (keyword or "").strip()
    if not kw:
        return 0

    rows = MonthlyVariableExpense.objects.filter(
        month=month,
        owner="HOUSE",
        var_type="CARD",
    ).order_by("id")

    total = 0

    for row in rows:
        texts: list[str] = []

        memo = getattr(row, "memo", None)
        if memo:
            texts.append(str(memo))

        category_obj = getattr(row, "category", None)
        category_name = getattr(category_obj, "name", None) if category_obj else None
        if category_name:
            texts.append(str(category_name))

        card_obj = getattr(row, "card", None)
        if card_obj:
            card_name = getattr(card_obj, "name", None)
            if card_name:
                texts.append(str(card_name))
            else:
                texts.append(str(card_obj))

        joined = " ".join(texts)
        if kw.lower() in joined.lower():
            total += _int(getattr(row, "amount", 0))

    return _int(total)


def _house_pension_insurance_fixed_sum() -> int:
    """
    何をする？
    - HOUSE の固定費テンプレから「年金」「保険」っぽいものを合算（重複計上しない）
    - 「年金/保険」など複合名でも、OR条件で1回だけ拾う
    """
    qs = FixedExpenseTemplate.objects.filter(is_active=True, owner="HOUSE").filter(
        Q(category__name__icontains="年金") |
        Q(category__name__icontains="保険") |
        Q(memo__icontains="年金") |
        Q(memo__icontains="保険")
    )
    return _sum_qs(qs, "amount")


def _month_diff_for_house_savings(month: date) -> int:
    """
    何をする？
    - 家の貯蓄の正式計算に使う「その月の差額」を返す。
    - snapshot があれば snapshot.diff を使う。
    - snapshot がなければ、その月の収入 - 支出を動的計算する。
    """
    snap = MonthlySnapshot.objects.filter(month=month).first()
    if snap:
        return _int(snap.diff)

    income = _sum_qs(MonthlyIncome.objects.filter(month=month), "amount")

    fixed = _sum_qs(
        FixedExpenseTemplate.objects.filter(is_active=True),
        "amount"
    )

    variable = _var_sum_for_expense(month)

    return _int(income - fixed - variable)


def _house_savings_actual(month: date) -> int:
    """
    何をする？
    - 家の貯蓄を正式計算で返す。
    - 2025年12月終了時点の固定値 -623,573 を起点にする。
    - 2026年1月以降、選択月までの月次差額を累計する。
    """
    m = month_first(month)

    if m <= HOUSE_SAVINGS_BASE_MONTH:
        return HOUSE_SAVINGS_BASE_VALUE

    total = HOUSE_SAVINGS_BASE_VALUE
    cur = add_month(HOUSE_SAVINGS_BASE_MONTH, 1)

    while cur <= m:
        total += _month_diff_for_house_savings(cur)
        cur = add_month(cur, 1)

    return _int(total)


def _next_todo_sort_order(month: date) -> int:
    """
    何をする？
    - 対象月の次の sort_order を返す
    """
    last = MonthlyTodo.objects.filter(month=month).order_by("-sort_order", "-id").first()
    if not last:
        return 10
    return _int(getattr(last, "sort_order", 0)) + 10


def _build_dynamic_month_values(request, today: date, m: date) -> dict:
    """
    何をする？
    - snapshot が無いときに使う「動的計算」一式をまとめて作る
    """
    total_income = _sum_qs(MonthlyIncome.objects.filter(month=m), "amount")

    fixed_sum = _sum_qs(
        FixedExpenseTemplate.objects.filter(is_active=True),
        "amount"
    )

    var_sum = _var_sum_for_expense(m)

    total_expense = _int(fixed_sum + var_sum)
    month_diff = _int(total_income - total_expense)

    rakuten_bank = _bank_balance_fallback(m, owner="B", name_contains="楽天銀行")
    aeon_bank = _bank_balance_fallback(m, owner="HOUSE", name_contains="イオン銀行")

    rakuten_bank_b = _int(rakuten_bank["balance"])
    aeon_bank_house = _int(aeon_bank["balance"])

    rakuten_cash_free = _portfolio_rakuten_available_from_cash_dashboard(today)
    rakuten_eval = _portfolio_rakuten_eval_like_rakuten(request.user)
    invest_total = _int(rakuten_cash_free + rakuten_eval)

    # 表示用の総資産：楽天銀行(B) + イオン銀行(家) + 楽天評価額
    total_assets = _int(rakuten_bank_b + aeon_bank_house + rakuten_eval)

    # 家の貯蓄：正式計算
    rakuten_bank_actual = _house_savings_actual(m)

    okodukai_b = _calc_okodukai(m, "B")
    okodukai_g = _calc_okodukai(m, "G")
    okodukai_total = _int(okodukai_b + okodukai_g)

    return {
        "total_income": total_income,
        "fixed_sum": fixed_sum,
        "var_sum": var_sum,
        "total_expense": total_expense,
        "month_diff": month_diff,

        "kpi_total_assets": total_assets,
        "kpi_rakuten_bank_actual": rakuten_bank_actual,
        "kpi_invest_total": invest_total,

        "rakuten_eval": rakuten_eval,
        "rakuten_cash_free": rakuten_cash_free,
        "rakuten_bank_b": rakuten_bank_b,
        "aeon_bank_house": aeon_bank_house,

        "okodukai_b": okodukai_b,
        "okodukai_g": okodukai_g,
        "okodukai_total": okodukai_total,

        "rakuten_bank_month_used": rakuten_bank.get("month"),
        "rakuten_bank_account_name_used": rakuten_bank.get("account_name"),
        "aeon_bank_month_used": aeon_bank.get("month"),
        "aeon_bank_account_name_used": aeon_bank.get("account_name"),
    }


@login_required
def dashboard(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    today = timezone.localdate()
    debug = (request.GET.get("debug") or "").strip() == "1"

    year_options = _year_options()

    req_year = request.GET.get("year") or request.POST.get("year")
    try:
        selected_year = int(req_year) if req_year else today.year
    except Exception:
        selected_year = today.year
    if selected_year not in year_options:
        year_options = sorted(list(set(year_options + [selected_year])), reverse=True)

    req_month = request.GET.get("month") or request.POST.get("month")
    selected_month_date = _parse_month_yyyy_mm(req_month) or month_first(today)
    m = month_first(selected_month_date)
    prev_m = add_month(m, -1)
    selected_month = f"{m.year}-{m.month:02d}"

    month_options = _month_options(today=today, months_back=24)

    if request.method == "POST" and (request.POST.get("action") or "").strip() == "confirm_snapshot":
        dyn = _build_dynamic_month_values(request, today=today, m=m)

        MonthlySnapshot.objects.update_or_create(
            month=m,
            defaults={
                "income": _int(dyn["total_income"]),
                "fixed": _int(dyn["fixed_sum"]),
                "variable": _int(dyn["var_sum"]),
                "expense_total": _int(dyn["total_expense"]),
                "diff": _int(dyn["month_diff"]),

                "kpi_total_assets": _int(dyn["kpi_total_assets"]),
                "kpi_rakuten_bank_actual": _int(dyn["kpi_rakuten_bank_actual"]),
                "kpi_invest_total": _int(dyn["kpi_invest_total"]),

                "rakuten_eval": _int(dyn["rakuten_eval"]),
                "rakuten_cash_free": _int(dyn["rakuten_cash_free"]),
                "rakuten_bank_b": _int(dyn["rakuten_bank_b"]),
                "aeon_bank_house": _int(dyn["aeon_bank_house"]),

                "locked_at": timezone.now(),
            }
        )

        q = f"?year={selected_year}&month={selected_month}"
        if debug:
            q += "&debug=1"
        return redirect(request.path + q)

    if request.method == "POST" and (request.POST.get("action") or "").strip() == "save_dashboard_memo":
        memo_text = request.POST.get("dashboard_memo", "")
        memo_text = memo_text.replace("\r\n", "\n").replace("\r", "\n")

        MonthlyDashboardMemo.objects.update_or_create(
            month=m,
            defaults={
                "memo": memo_text,
            }
        )

        q = f"?year={selected_year}&month={selected_month}"
        if debug:
            q += "&debug=1"
        return redirect(request.path + q)

    if request.method == "POST" and (request.POST.get("action") or "").strip() == "add_monthly_todo":
        title = (request.POST.get("todo_title") or "").strip()
        note = (request.POST.get("todo_note") or "").strip()

        if title:
            MonthlyTodo.objects.create(
                month=m,
                title=title,
                note=note,
                status=MonthlyTodo.STATUS_TODO,
                sort_order=_next_todo_sort_order(m),
                completed_at=None,
            )

        q = f"?year={selected_year}&month={selected_month}"
        if debug:
            q += "&debug=1"
        return redirect(request.path + q)

    if request.method == "POST" and (request.POST.get("action") or "").strip() == "toggle_monthly_todo_done":
        todo_id = request.POST.get("todo_id")
        todo = MonthlyTodo.objects.filter(id=todo_id, month=m).first()
        if todo:
            if todo.status == MonthlyTodo.STATUS_DONE:
                todo.status = MonthlyTodo.STATUS_TODO
                todo.completed_at = None
            else:
                todo.status = MonthlyTodo.STATUS_DONE
                todo.completed_at = timezone.now()
            todo.save(update_fields=["status", "completed_at", "updated_at"])

        q = f"?year={selected_year}&month={selected_month}"
        if debug:
            q += "&debug=1"
        return redirect(request.path + q)

    if request.method == "POST" and (request.POST.get("action") or "").strip() == "update_monthly_todo_status":
        todo_id = request.POST.get("todo_id")
        new_status = (request.POST.get("todo_status") or "").strip()

        if new_status not in [MonthlyTodo.STATUS_TODO, MonthlyTodo.STATUS_DOING, MonthlyTodo.STATUS_DONE]:
            new_status = MonthlyTodo.STATUS_TODO

        todo = MonthlyTodo.objects.filter(id=todo_id, month=m).first()
        if todo:
            todo.status = new_status
            if new_status == MonthlyTodo.STATUS_DONE:
                todo.completed_at = timezone.now()
            else:
                todo.completed_at = None
            todo.save(update_fields=["status", "completed_at", "updated_at"])

        q = f"?year={selected_year}&month={selected_month}"
        if debug:
            q += "&debug=1"
        return redirect(request.path + q)

    if request.method == "POST" and (request.POST.get("action") or "").strip() == "delete_monthly_todo":
        todo_id = request.POST.get("todo_id")
        todo = MonthlyTodo.objects.filter(id=todo_id, month=m).first()
        if todo:
            todo.delete()

        q = f"?year={selected_year}&month={selected_month}"
        if debug:
            q += "&debug=1"
        return redirect(request.path + q)

    snap = MonthlySnapshot.objects.filter(month=m).first()
    is_snapshot = bool(snap)

    if snap:
        total_income = _int(snap.income)
        fixed_sum = _int(snap.fixed)
        var_sum = _int(snap.variable)
        total_expense = _int(snap.expense_total)
        month_diff = _int(snap.diff)

        rakuten_eval = _int(snap.rakuten_eval)
        rakuten_cash_free = _int(snap.rakuten_cash_free)
        rakuten_bank_b = _int(snap.rakuten_bank_b)
        aeon_bank_house = _int(snap.aeon_bank_house)

        # ここが重要：
        # snapshot に古いKPI値が残っていても、表示用は正式計算で上書きする
        total_assets = _int(rakuten_bank_b + aeon_bank_house + rakuten_eval)
        rakuten_bank_actual = _house_savings_actual(m)
        invest_total = _int(rakuten_cash_free + rakuten_eval)

        rakuten_bank_month_used = None
        rakuten_bank_account_name_used = None
        aeon_bank_month_used = None
        aeon_bank_account_name_used = None

        okodukai_b = _calc_okodukai(m, "B")
        okodukai_g = _calc_okodukai(m, "G")
        okodukai_total = _int(okodukai_b + okodukai_g)
    else:
        dyn = _build_dynamic_month_values(request, today=today, m=m)

        total_income = dyn["total_income"]
        fixed_sum = dyn["fixed_sum"]
        var_sum = dyn["var_sum"]
        total_expense = dyn["total_expense"]
        month_diff = dyn["month_diff"]

        total_assets = dyn["kpi_total_assets"]
        rakuten_bank_actual = dyn["kpi_rakuten_bank_actual"]
        invest_total = dyn["kpi_invest_total"]

        rakuten_eval = dyn["rakuten_eval"]
        rakuten_cash_free = dyn["rakuten_cash_free"]
        rakuten_bank_b = dyn["rakuten_bank_b"]
        aeon_bank_house = dyn["aeon_bank_house"]

        okodukai_b = dyn["okodukai_b"]
        okodukai_g = dyn["okodukai_g"]
        okodukai_total = dyn["okodukai_total"]

        rakuten_bank_month_used = dyn["rakuten_bank_month_used"]
        rakuten_bank_account_name_used = dyn["rakuten_bank_account_name_used"]
        aeon_bank_month_used = dyn["aeon_bank_month_used"]
        aeon_bank_account_name_used = dyn["aeon_bank_account_name_used"]

    dashboard_memo_obj = MonthlyDashboardMemo.objects.filter(month=m).first()
    dashboard_memo = getattr(dashboard_memo_obj, "memo", "") or ""

    monthly_todos = list(
        MonthlyTodo.objects
        .filter(month=m)
        .order_by("sort_order", "id")
    )
    monthly_todos = sorted(
        monthly_todos,
        key=lambda x: (
            1 if x.status == MonthlyTodo.STATUS_DONE else 0,
            _int(getattr(x, "sort_order", 0)),
            _int(getattr(x, "id", 0)),
        )
    )

    monthly_todo_count_total = len(monthly_todos)
    monthly_todo_count_done = len([x for x in monthly_todos if x.status == MonthlyTodo.STATUS_DONE])
    monthly_todo_count_open = monthly_todo_count_total - monthly_todo_count_done

    bank_groups, bank_totals = _bank_groups_all_accounts(month=m)

    chiba_withdraw_rows: list[dict] = []
    try:
        chiba_accs = (
            Account.objects
            .filter(kind="ACCOUNT", name__icontains="千葉")
            .order_by("owner", "id")
        )
        for acc in chiba_accs:
            owner = (acc.owner or "").strip()
            if owner not in ["HOUSE", "B", "G"]:
                continue

            info = _account_balance_fallback_for_account(month=m, acc=acc)
            bal = _int(info["balance"])

            withdrawable = (bal // 1000) * 1000
            remain = _int(bal - withdrawable)

            chiba_withdraw_rows.append({
                "owner": owner,
                "owner_label": _owner_label(owner),
                "name": info.get("account_name") or (acc.name or "").strip(),
                "balance": bal,
                "withdrawable": _int(withdrawable),
                "remain": remain,
                "month": info.get("month"),
            })
    except Exception:
        chiba_withdraw_rows = []

    smbc_b_balance = 0
    smbc_b_month = None
    try:
        smbc_acc_b = (
            Account.objects
            .filter(kind="ACCOUNT", owner="B")
            .filter(name__icontains="三井住友")
            .order_by("id")
            .first()
        )
        if smbc_acc_b:
            info = _account_balance_fallback_for_account(month=m, acc=smbc_acc_b)
            smbc_b_balance = _int(info.get("balance"))
            smbc_b_month = info.get("month")
    except Exception:
        smbc_b_balance = 0
        smbc_b_month = None

    smbc_b_need = _int(
        _allowance_fixed_sum("B")
        + _advance_var_sum(m, "B")
        + _advance_fixed_sum("B")
    )
    smbc_b_delta = _int(smbc_b_need - smbc_b_balance)

    rakuten_g_balance = 0
    rakuten_g_month = None
    try:
        rakuten_acc_g = (
            Account.objects
            .filter(kind="ACCOUNT", owner="G")
            .filter(name__icontains="楽天銀行")
            .order_by("id")
            .first()
        )
        if rakuten_acc_g:
            info = _account_balance_fallback_for_account(month=m, acc=rakuten_acc_g)
            rakuten_g_balance = _int(info.get("balance"))
            rakuten_g_month = info.get("month")
    except Exception:
        rakuten_g_balance = 0
        rakuten_g_month = None

    rakuten_g_need = _int(_calc_okodukai(m, "G"))
    rakuten_g_delta = _int(rakuten_g_need - rakuten_g_balance)

    aeon_house_balance = 0
    aeon_house_month = None
    aeon_house_account_name = None
    try:
        aeon_info = _bank_balance_fallback(m, owner="HOUSE", name_contains="イオン銀行")
        aeon_house_balance = _int(aeon_info.get("balance"))
        aeon_house_month = aeon_info.get("month")
        aeon_house_account_name = aeon_info.get("account_name")
    except Exception:
        aeon_house_balance = _int(aeon_bank_house)
        aeon_house_month = None
        aeon_house_account_name = None

    house_bill_epos = _house_card_bill_sum_by_keyword(m, "エポス")
    house_bill_aeon = _house_card_bill_sum_by_keyword(m, "イオン")
    house_bill_yodobashi = _house_card_bill_sum_by_keyword(m, "ヨドバシ")

    house_pension_insurance = _house_pension_insurance_fixed_sum()

    aeon_house_required_min = _int(
        house_bill_epos
        + house_bill_aeon
        + house_bill_yodobashi
        + house_pension_insurance
    )
    aeon_house_delta = _int(aeon_house_required_min - aeon_house_balance)

    prev_income = _sum_qs(MonthlyIncome.objects.filter(month=prev_m), "amount")
    prev_var = _var_sum_for_expense(prev_m)

    template_fixed_now = _sum_qs(FixedExpenseTemplate.objects.filter(is_active=True), "amount")
    prev_fixed = template_fixed_now
    prev_expense = _int(prev_fixed + prev_var)

    d_income = _delta(total_income, prev_income)
    d_expense = _delta(total_expense, prev_expense)
    d_fixed = _delta(fixed_sum, prev_fixed)
    d_var = _delta(var_sum, prev_var)

    dp_income = _delta_pct(total_income, prev_income)
    dp_expense = _delta_pct(total_expense, prev_expense)
    dp_fixed = _delta_pct(fixed_sum, prev_fixed)
    dp_var = _delta_pct(var_sum, prev_var)

    # KPI 先月比：正式計算で比較する
    prev_snap = MonthlySnapshot.objects.filter(month=prev_m).first()
    if prev_snap:
        prev_rakuten_eval = _int(prev_snap.rakuten_eval)
        prev_rakuten_cash_free = _int(prev_snap.rakuten_cash_free)
        prev_rakuten_bank_b = _int(prev_snap.rakuten_bank_b)
        prev_aeon_bank_house = _int(prev_snap.aeon_bank_house)

        prev_total_assets = _int(prev_rakuten_bank_b + prev_aeon_bank_house + prev_rakuten_eval)
        prev_rakuten_bank_actual = _house_savings_actual(prev_m)
        prev_invest_total = _int(prev_rakuten_cash_free + prev_rakuten_eval)
    else:
        prev_dyn = _build_dynamic_month_values(request, today=today, m=prev_m)
        prev_total_assets = _int(prev_dyn["kpi_total_assets"])
        prev_rakuten_bank_actual = _int(prev_dyn["kpi_rakuten_bank_actual"])
        prev_invest_total = _int(prev_dyn["kpi_invest_total"])

    d_total_assets = _delta(total_assets, prev_total_assets)
    d_rakuten_bank_actual = _delta(rakuten_bank_actual, prev_rakuten_bank_actual)
    d_invest_total = _delta(invest_total, prev_invest_total)

    dp_total_assets = _delta_pct(total_assets, prev_total_assets)
    dp_rakuten_bank_actual = _delta_pct(rakuten_bank_actual, prev_rakuten_bank_actual)
    dp_invest_total = _delta_pct(invest_total, prev_invest_total)

    snaps_year = list(MonthlySnapshot.objects.filter(month__year=selected_year))
    if snaps_year:
        year_income = _int(sum(_int(s.income) for s in snaps_year))
        year_expense = _int(sum(_int(s.expense_total) for s in snaps_year))
        year_diff = _int(sum(_int(s.diff) for s in snaps_year))
        year_is_snapshot = True
    else:
        year_income = _int(
            MonthlyIncome.objects
            .filter(month__year=selected_year)
            .aggregate(s=Sum("amount"))["s"] or 0
        )

        year_var = _int(
            MonthlyVariableExpense.objects
            .filter(month__year=selected_year)
            .exclude(var_type="CARD", owner__in=["B", "G"])
            .aggregate(s=Sum("amount"))["s"] or 0
        )

        year_expense = _int(year_var + template_fixed_now)
        year_diff = _int(year_income - year_expense)
        year_is_snapshot = False

    context = {
        "title": "家計簿",
        "debug": debug,

        "is_snapshot": is_snapshot,
        "snapshot_locked_at": getattr(snap, "locked_at", None),

        "year_options": year_options,
        "selected_year": selected_year,
        "month_options": month_options,
        "selected_month": selected_month,

        "month_label": f"{m.year}-{m.month:02d}",
        "prev_month_label": f"{prev_m.year}-{prev_m.month:02d}",

        "kpi_total_assets": total_assets,
        "kpi_rakuten_bank_actual": rakuten_bank_actual,
        "kpi_invest_total": invest_total,

        "d_total_assets": d_total_assets,
        "dp_total_assets": dp_total_assets,
        "d_rakuten_bank_actual": d_rakuten_bank_actual,
        "dp_rakuten_bank_actual": dp_rakuten_bank_actual,
        "d_invest_total": d_invest_total,
        "dp_invest_total": dp_invest_total,

        "rakuten_cash_free": rakuten_cash_free,
        "rakuten_eval": rakuten_eval,
        "rakuten_bank_b": rakuten_bank_b,
        "aeon_bank_house": aeon_bank_house,

        "rakuten_bank_month_used": rakuten_bank_month_used,
        "rakuten_bank_account_name_used": rakuten_bank_account_name_used,
        "aeon_bank_month_used": aeon_bank_month_used,
        "aeon_bank_account_name_used": aeon_bank_account_name_used,

        "year_label": f"{selected_year}",
        "year_income": year_income,
        "year_expense": year_expense,
        "year_diff": year_diff,
        "year_is_snapshot": year_is_snapshot,

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

        "okodukai_b": okodukai_b,
        "okodukai_g": okodukai_g,
        "okodukai_total": okodukai_total,

        "bank_groups": bank_groups,
        "bank_totals": bank_totals,

        "chiba_withdraw_rows": chiba_withdraw_rows,

        "smbc_b_balance": smbc_b_balance,
        "smbc_b_month": smbc_b_month,
        "smbc_b_need": smbc_b_need,
        "smbc_b_delta": smbc_b_delta,

        "rakuten_g_balance": rakuten_g_balance,
        "rakuten_g_month": rakuten_g_month,
        "rakuten_g_need": rakuten_g_need,
        "rakuten_g_delta": rakuten_g_delta,

        "aeon_house_balance": aeon_house_balance,
        "aeon_house_month": aeon_house_month,
        "aeon_house_account_name": aeon_house_account_name,

        "house_bill_epos": house_bill_epos,
        "house_bill_aeon": house_bill_aeon,
        "house_bill_yodobashi": house_bill_yodobashi,
        "house_pension_insurance": house_pension_insurance,

        "aeon_house_required_min": aeon_house_required_min,
        "aeon_house_delta": aeon_house_delta,

        "dashboard_memo": dashboard_memo,

        "monthly_todos": monthly_todos,
        "monthly_todo_count_total": monthly_todo_count_total,
        "monthly_todo_count_open": monthly_todo_count_open,
        "monthly_todo_count_done": monthly_todo_count_done,
        "todo_status_done": MonthlyTodo.STATUS_DONE,
    }

    return render(request, "kakeibo/dashboard.html", context)