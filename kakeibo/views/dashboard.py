# =========================================
# [FILE] dashboard.py
# [PATH] kakeibo/views/dashboard.py
#
# このファイルは何？
# 家計簿トップ画面（/kakeibo/）のビュー。
# - KPI：総資産 / 楽天銀行(B)の実残高 / 投資（評価額＋現金余力）
# - 年度（選択可）の収支
# - 月（選択可）の収入・支出・差額＋固定/変動内訳 + 先月比
# - ✅ 銀行残高：登録済み口座を全部（HOUSE / B / G）で表示
#   - 当月が無い口座は「最新月（選択月以前）」で表示
#   - Bの楽天銀行はトップで見えてるので、銀行残高セクションから除外
#
# ★今回の修正ポイント（あなたの最新HTMLに対応）
# 1) 「月の可視化（バー）」は削除された前提なので、view側もその値を出さない
# 2) 「銀行残高」セクション用に、bank_groups / bank_totals を context に追加
# 3) bank_groups は Account(kind="ACCOUNT") を owner(HOUSE/B/G) ごとにまとめる
#    - ただし owner="B" かつ nameに「楽天銀行」を含む口座は bank_groups から除外
# 4) 既存のKPI/収支/先月比/お小遣いロジックは維持（テンプレ側の表示に合わせる）
# =========================================

from decimal import Decimal
from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
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
)


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


def _bank_groups_all_accounts(month: date) -> tuple[dict, dict]:
    """
    何をする？
    - 銀行残高セクション用に、登録済み口座を owner(HOUSE/B/G) ごとにまとめる。
    - 各口座は「選択月が無ければ、選択月以前の最新月」で残高を表示。
    - Bの楽天銀行はトップで見えてるため、このリストから除外する（name に '楽天銀行' を含む）。
    戻り値：
      - bank_groups: {"HOUSE":[{name,balance,month},...], "B":[...], "G":[...]}
      - bank_totals: {"HOUSE":合計, "B":合計, "G":合計}
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

            # ✅ Bの楽天銀行はトップで見えてるので除外
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
                # 想定外ownerは捨てる（安全側）
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


# -----------------------
# ✅ 個人カード除外ルールつき集計ヘルパ
# -----------------------
def _var_sum_for_expense(month: date) -> int:
    """
    何をする？
    - 支出に含める「変動費合計」を返す
    - ルール：B/G の CARD は除外（登録はするが支出に含めない）
             HOUSE の CARD は含める
             ADVANCE は含める（あなたの指定）
    """
    qs = MonthlyVariableExpense.objects.filter(month=month)
    qs = qs.exclude(var_type="CARD", owner__in=["B", "G"])
    return _sum_qs(qs, "amount")


def _card_bill_sum(month: date, owner: str) -> int:
    """
    何をする？
    - owner(B/G/HOUSE) の「カード合計請求額」を返す（CARD は支出含む/含まないとは別）
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
    - category.name が前提（あなたの画面上の分類名に合わせる）
    """
    qs = FixedExpenseTemplate.objects.filter(is_active=True, owner=owner, category__name__icontains=contains)
    return _sum_qs(qs, "amount")


def _fixed_sum_memo_contains(owner: str, contains: str) -> int:
    """
    何をする？
    - 固定費テンプレから、memo に contains を含むものを合計（保険として）
    """
    qs = FixedExpenseTemplate.objects.filter(is_active=True, owner=owner, memo__icontains=contains)
    return _sum_qs(qs, "amount")


def _allowance_fixed_sum(owner: str) -> int:
    """
    何をする？
    - 「固定費で登録したそれぞれのお小遣い」合計
    - 優先：分類名に「お小遣い」
    - 予備：memoに「お小遣い」
    """
    s = _fixed_sum_category_contains(owner, "お小遣い")
    if s:
        return s
    return _fixed_sum_memo_contains(owner, "お小遣い")


def _advance_fixed_sum(owner: str) -> int:
    """
    何をする？
    - 固定費側の「立替」合計（分類名が「立替」）
    """
    s = _fixed_sum_category_contains(owner, "立替")
    if s:
        return s
    return _fixed_sum_memo_contains(owner, "立替")


def _calc_okodukai(month: date, owner: str) -> int:
    """
    何をする？
    - お小遣い（自動計算）を返す（★最新式）
      （固定費お小遣い）＋（立替合計）−（カード請求合計）

      立替合計 = 変動ADVANCE + 固定(立替)
      カード請求合計 = 変動CARD
    """
    fixed_allow = _allowance_fixed_sum(owner)
    adv_total = _int(_advance_var_sum(month, owner) + _advance_fixed_sum(owner))
    bill = _card_bill_sum(month, owner)
    return _int(fixed_allow + adv_total - bill)


def _build_dynamic_month_values(request, today: date, m: date) -> dict:
    """
    何をする？
    - snapshot が無いときに使う「動的計算」一式をまとめて作る
    - ここで作った値を snapshot 保存にも流用する（確定時に同じ値が保存される）
    """
    # kakeibo：選択月
    total_income = _sum_qs(MonthlyIncome.objects.filter(month=m), "amount")

    fixed_sum = _sum_qs(
        FixedExpenseTemplate.objects.filter(is_active=True),
        "amount"
    )

    # ✅ 変動費（支出に含める分）：個人カード(B/GのCARD)は除外
    var_sum = _var_sum_for_expense(m)

    total_expense = _int(fixed_sum + var_sum)
    month_diff = _int(total_income - total_expense)

    # 銀行（家計簿）：選択月が無ければ最新月で拾う（KPI用）
    rakuten_bank = _bank_balance_fallback(m, owner="B", name_contains="楽天銀行")
    aeon_bank = _bank_balance_fallback(m, owner="HOUSE", name_contains="イオン銀行")

    rakuten_bank_b = _int(rakuten_bank["balance"])
    aeon_bank_house = _int(aeon_bank["balance"])

    # portfolio（楽天：投資） ※「確定時点の画面値」を保存したいので today ベース
    rakuten_cash_free = _portfolio_rakuten_available_from_cash_dashboard(today)
    rakuten_eval = _portfolio_rakuten_eval_like_rakuten(request.user)
    invest_total = _int(rakuten_cash_free + rakuten_eval)

    # KPI
    total_assets = _int(rakuten_bank_b + aeon_bank_house + invest_total)

    # あなたのルール：実残高 = 4,136,736 - 余力 - 楽天銀行(B)
    rakuten_bank_actual = _int(4_136_736 - rakuten_cash_free - rakuten_bank_b)

    # ✅ お小遣い（B/G）
    okodukai_b = _calc_okodukai(m, "B")
    okodukai_g = _calc_okodukai(m, "G")
    okodukai_total = _int(okodukai_b + okodukai_g)

    return {
        # 月収支
        "total_income": total_income,
        "fixed_sum": fixed_sum,
        "var_sum": var_sum,
        "total_expense": total_expense,
        "month_diff": month_diff,

        # KPI
        "kpi_total_assets": total_assets,
        "kpi_rakuten_bank_actual": rakuten_bank_actual,
        "kpi_invest_total": invest_total,

        # 内訳
        "rakuten_eval": rakuten_eval,
        "rakuten_cash_free": rakuten_cash_free,
        "rakuten_bank_b": rakuten_bank_b,
        "aeon_bank_house": aeon_bank_house,

        # お小遣い
        "okodukai_b": okodukai_b,
        "okodukai_g": okodukai_g,
        "okodukai_total": okodukai_total,

        # debug用（どの月の残高を拾ったか）
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

    # -----------------------
    # 選択（GET / POST）
    # -----------------------
    year_options = _year_options()

    # year
    req_year = request.GET.get("year") or request.POST.get("year")
    try:
        selected_year = int(req_year) if req_year else today.year
    except Exception:
        selected_year = today.year
    if selected_year not in year_options:
        year_options = sorted(list(set(year_options + [selected_year])), reverse=True)

    # month
    req_month = request.GET.get("month") or request.POST.get("month")
    selected_month_date = _parse_month_yyyy_mm(req_month) or month_first(today)
    m = month_first(selected_month_date)
    prev_m = add_month(m, -1)
    selected_month = f"{m.year}-{m.month:02d}"

    month_options = _month_options(today=today, months_back=24)

    # -----------------------
    # POST：この月を確定（作成/上書き）
    # -----------------------
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

    # -----------------------
    # 表示（選択月）：snapshot があればそれを正にする
    # -----------------------
    snap = MonthlySnapshot.objects.filter(month=m).first()
    is_snapshot = bool(snap)

    if snap:
        total_income = _int(snap.income)
        fixed_sum = _int(snap.fixed)
        var_sum = _int(snap.variable)
        total_expense = _int(snap.expense_total)
        month_diff = _int(snap.diff)

        total_assets = _int(snap.kpi_total_assets)
        rakuten_bank_actual = _int(snap.kpi_rakuten_bank_actual)
        invest_total = _int(snap.kpi_invest_total)

        rakuten_eval = _int(snap.rakuten_eval)
        rakuten_cash_free = _int(snap.rakuten_cash_free)
        rakuten_bank_b = _int(snap.rakuten_bank_b)
        aeon_bank_house = _int(snap.aeon_bank_house)

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

    # -----------------------
    # ✅ 銀行残高（登録済み口座を全部 / HOUSE・B・G）
    # - 当月が無い口座は「最新月（選択月以前）」で拾う
    # - Bの楽天銀行はトップ表示があるため除外
    # -----------------------
    bank_groups, bank_totals = _bank_groups_all_accounts(month=m)

    # -----------------------
    # 先月比（選択月に対して）
    # -----------------------
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

    # -----------------------
    # ✅ KPI 先月比（総資産 / 楽天銀行残高 / 投資）
    # -----------------------
    prev_snap = MonthlySnapshot.objects.filter(month=prev_m).first()
    if prev_snap:
        prev_total_assets = _int(prev_snap.kpi_total_assets)
        prev_rakuten_bank_actual = _int(prev_snap.kpi_rakuten_bank_actual)
        prev_invest_total = _int(prev_snap.kpi_invest_total)
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

    # -----------------------
    # 年度（選択year）
    # -----------------------
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

        # KPI
        "kpi_total_assets": total_assets,
        "kpi_rakuten_bank_actual": rakuten_bank_actual,
        "kpi_invest_total": invest_total,

        # ✅ KPI 先月比
        "d_total_assets": d_total_assets,
        "dp_total_assets": dp_total_assets,
        "d_rakuten_bank_actual": d_rakuten_bank_actual,
        "dp_rakuten_bank_actual": dp_rakuten_bank_actual,
        "d_invest_total": d_invest_total,
        "dp_invest_total": dp_invest_total,

        # 内訳（debugにも使う）
        "rakuten_cash_free": rakuten_cash_free,
        "rakuten_eval": rakuten_eval,
        "rakuten_bank_b": rakuten_bank_b,
        "aeon_bank_house": aeon_bank_house,

        "rakuten_bank_month_used": rakuten_bank_month_used,
        "rakuten_bank_account_name_used": rakuten_bank_account_name_used,
        "aeon_bank_month_used": aeon_bank_month_used,
        "aeon_bank_account_name_used": aeon_bank_account_name_used,

        # 年度（選択）
        "year_label": f"{selected_year}",
        "year_income": year_income,
        "year_expense": year_expense,
        "year_diff": year_diff,
        "year_is_snapshot": year_is_snapshot,

        # 月（選択）
        "total_income": total_income,
        "total_expense": total_expense,
        "month_diff": month_diff,
        "fixed_sum": fixed_sum,
        "var_sum": var_sum,

        # 先月比（選択月に対して）
        "d_income": d_income,
        "d_expense": d_expense,
        "d_fixed": d_fixed,
        "d_var": d_var,
        "dp_income": dp_income,
        "dp_expense": dp_expense,
        "dp_fixed": dp_fixed,
        "dp_var": dp_var,

        # お小遣い
        "okodukai_b": okodukai_b,
        "okodukai_g": okodukai_g,
        "okodukai_total": okodukai_total,

        # ✅ 銀行残高（新セクション用）
        "bank_groups": bank_groups,
        "bank_totals": bank_totals,
    }

    return render(request, "kakeibo/dashboard.html", context)