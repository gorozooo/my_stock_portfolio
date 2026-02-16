# =========================================
# [FILE] manage.py
# [PATH] kakeibo/views/manage.py
#
# このファイルは何？
# 「管理（探して編集/削除）」専用の画面群。
# - 入力画面とは分離して、データが増えても探しやすいようにする。
# - 月次データは YYYY-MM で絞り込みできる（デフォルトは当月）。
#
# ★今回の改善
# - owner を前回選択から自動復元（種類ごとに session に保存）
# - month も前回選択から自動復元（月次系のみ）
# - 編集/削除後も、同じ month/owner に戻る
#
# ★今回：更新/削除が成功したら「何を」までトースト表示する
# =========================================

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone

from .permissions import kakeibo_access_required
from ..forms import (
    MonthlyIncomeForm,
    MonthlyVariableExpenseForm,
    FixedExpenseTemplateForm,
    BankBalanceForm,
)
from ..models import (
    MonthlyIncome,
    MonthlyVariableExpense,
    FixedExpenseTemplate,
    BankBalance,
)


def _owner_label(owner: str) -> str:
    m = {
        "HOUSE": "家",
        "B": "ぼーや",
        "G": "ごろ",
    }
    return m.get(owner, owner or "不明")


def _yen(amount) -> str:
    if amount is None:
        return ""
    try:
        return f"{int(amount):,}"
    except Exception:
        return f"{amount}"


# -----------------------------
# session key helpers
# -----------------------------
def _sess_key_owner(kind: str) -> str:
    return f"kakeibo_manage_owner_{kind}"


def _sess_key_month(kind: str) -> str:
    return f"kakeibo_manage_month_{kind}"


def _get_owner(request, kind: str) -> str:
    owner = (request.GET.get("owner") or "").strip()
    if owner == "":
        owner = (request.session.get(_sess_key_owner(kind)) or "").strip()
    request.session[_sess_key_owner(kind)] = owner
    return owner


def _month_from_get_or_session(request, kind: str):
    today = timezone.localdate().replace(day=1)

    s = (request.GET.get("month") or "").strip()
    if not s:
        s = (request.session.get(_sess_key_month(kind)) or "").strip()

    if not s:
        d = today
    else:
        try:
            y, m = s.split("-")
            y = int(y)
            m = int(m)
            d = today.replace(year=y, month=m, day=1)
        except Exception:
            d = today

    request.session[_sess_key_month(kind)] = d.strftime("%Y-%m")
    return d


def _month_str(d) -> str:
    return d.strftime("%Y-%m")


def _add_month(d, delta: int):
    y = d.year
    m = d.month + delta
    while m <= 0:
        y -= 1
        m += 12
    while m >= 13:
        y += 1
        m -= 12
    return d.replace(year=y, month=m, day=1)


@login_required
def manage_menu(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    return render(request, "kakeibo/manage_menu.html", {
        "title": "管理（探して編集・削除）",
    })


@login_required
def manage_income(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    kind = "income"
    month = _month_from_get_or_session(request, kind)
    owner = _get_owner(request, kind)

    qs = MonthlyIncome.objects.filter(month=month).order_by("-id")
    if owner:
        qs = qs.filter(owner=owner)

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(MonthlyIncome, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "update":
            obj = get_object_or_404(MonthlyIncome, id=request.POST.get("id"))
            form = MonthlyIncomeForm(request.POST, instance=obj)
            if form.is_valid():
                x = form.save()
                m = getattr(x, "month", None)
                m_s = m.strftime("%Y-%m") if m else ""
                messages.success(
                    request,
                    f"✅ 収入を更新：{m_s} / {_owner_label(getattr(x,'owner',''))} / {getattr(getattr(x,'category',None),'name','')} / ¥{_yen(getattr(x,'amount',None))}",
                )
                return redirect(f"/kakeibo/manage/income/?month={_month_str(month)}&owner={owner}")

        elif action == "delete":
            obj = get_object_or_404(MonthlyIncome, id=request.POST.get("id"))
            m = getattr(obj, "month", None)
            m_s = m.strftime("%Y-%m") if m else ""
            msg = f"✅ 収入を削除：{m_s} / {_owner_label(getattr(obj,'owner',''))} / {getattr(getattr(obj,'category',None),'name','')} / ¥{_yen(getattr(obj,'amount',None))}"
            obj.delete()
            messages.success(request, msg)
            return redirect(f"/kakeibo/manage/income/?month={_month_str(month)}&owner={owner}")

    form = MonthlyIncomeForm(instance=edit_obj) if (edit_mode and edit_obj) else None

    return render(request, "kakeibo/manage_month_list.html", {
        "title": "収入（管理）",
        "kind": "income",
        "month": month,
        "prev_month": _add_month(month, -1),
        "next_month": _add_month(month, 1),
        "owner": owner,
        "rows": qs,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "form": form,
    })


@login_required
def manage_variable(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    kind = "variable"
    month = _month_from_get_or_session(request, kind)
    owner = _get_owner(request, kind)

    qs = MonthlyVariableExpense.objects.filter(month=month).order_by("-id")
    if owner:
        qs = qs.filter(owner=owner)

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(MonthlyVariableExpense, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "update":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))
            form = MonthlyVariableExpenseForm(request.POST, instance=obj)
            if form.is_valid():
                x = form.save()
                m = getattr(x, "month", None)
                m_s = m.strftime("%Y-%m") if m else ""
                var_type = getattr(x, "var_type", "")
                vt = "カード" if var_type == "CARD" else ("立替" if var_type == "ADVANCE" else var_type)
                card_name = getattr(getattr(x, "card", None), "name", "")
                tail = f" / {card_name}" if (var_type == "CARD" and card_name) else ""
                messages.success(
                    request,
                    f"✅ 変動費を更新：{m_s} / {_owner_label(getattr(x,'owner',''))} / {vt}{tail} / ¥{_yen(getattr(x,'amount',None))}",
                )
                return redirect(f"/kakeibo/manage/variable/?month={_month_str(month)}&owner={owner}")

        elif action == "delete":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))
            m = getattr(obj, "month", None)
            m_s = m.strftime("%Y-%m") if m else ""
            var_type = getattr(obj, "var_type", "")
            vt = "カード" if var_type == "CARD" else ("立替" if var_type == "ADVANCE" else var_type)
            card_name = getattr(getattr(obj, "card", None), "name", "")
            tail = f" / {card_name}" if (var_type == "CARD" and card_name) else ""
            msg = f"✅ 変動費を削除：{m_s} / {_owner_label(getattr(obj,'owner',''))} / {vt}{tail} / ¥{_yen(getattr(obj,'amount',None))}"
            obj.delete()
            messages.success(request, msg)
            return redirect(f"/kakeibo/manage/variable/?month={_month_str(month)}&owner={owner}")

    form = MonthlyVariableExpenseForm(instance=edit_obj) if (edit_mode and edit_obj) else None

    return render(request, "kakeibo/manage_month_list.html", {
        "title": "変動費（管理）",
        "kind": "variable",
        "month": month,
        "prev_month": _add_month(month, -1),
        "next_month": _add_month(month, 1),
        "owner": owner,
        "rows": qs,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "form": form,
    })


@login_required
def manage_bank(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    kind = "bank"
    month = _month_from_get_or_session(request, kind)
    owner = _get_owner(request, kind)

    qs = BankBalance.objects.filter(month=month).select_related("account").order_by("-id")
    if owner:
        qs = qs.filter(account__owner=owner)

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(BankBalance, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "update":
            obj = get_object_or_404(BankBalance, id=request.POST.get("id"))
            form = BankBalanceForm(request.POST, instance=obj)
            if form.is_valid():
                x = form.save()
                m = getattr(x, "month", None)
                m_s = m.strftime("%Y-%m") if m else ""
                acc = getattr(x, "account", None)
                acc_name = getattr(acc, "name", "")
                bal = getattr(x, "balance", None)
                messages.success(request, f"✅ 銀行残高を更新：{m_s} / {acc_name} / ¥{_yen(bal)}")
                return redirect(f"/kakeibo/manage/bank/?month={_month_str(month)}&owner={owner}")

        elif action == "delete":
            obj = get_object_or_404(BankBalance, id=request.POST.get("id"))
            m = getattr(obj, "month", None)
            m_s = m.strftime("%Y-%m") if m else ""
            acc = getattr(obj, "account", None)
            acc_name = getattr(acc, "name", "")
            bal = getattr(obj, "balance", None)
            msg = f"✅ 銀行残高を削除：{m_s} / {acc_name} / ¥{_yen(bal)}"
            obj.delete()
            messages.success(request, msg)
            return redirect(f"/kakeibo/manage/bank/?month={_month_str(month)}&owner={owner}")

    form = BankBalanceForm(instance=edit_obj) if (edit_mode and edit_obj) else None

    return render(request, "kakeibo/manage_month_list.html", {
        "title": "銀行残高（管理）",
        "kind": "bank",
        "month": month,
        "prev_month": _add_month(month, -1),
        "next_month": _add_month(month, 1),
        "owner": owner,
        "rows": qs,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "form": form,
    })


@login_required
def manage_fixed(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    kind = "fixed"
    owner = _get_owner(request, kind)

    qs = FixedExpenseTemplate.objects.order_by("-is_active", "id")
    if owner:
        qs = qs.filter(owner=owner)

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(FixedExpenseTemplate, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "update":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))
            form = FixedExpenseTemplateForm(request.POST, instance=obj)
            if form.is_valid():
                x = form.save()
                messages.success(
                    request,
                    f"✅ 固定費を更新：{_owner_label(getattr(x,'owner',''))} / {getattr(getattr(x,'category',None),'name','')} / ¥{_yen(getattr(x,'amount',None))}",
                )
                return redirect(f"/kakeibo/manage/fixed/?owner={owner}")

        elif action == "delete":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))
            msg = f"✅ 固定費を削除：{_owner_label(getattr(obj,'owner',''))} / {getattr(getattr(obj,'category',None),'name','')} / ¥{_yen(getattr(obj,'amount',None))}"
            obj.delete()
            messages.success(request, msg)
            return redirect(f"/kakeibo/manage/fixed/?owner={owner}")

    form = FixedExpenseTemplateForm(instance=edit_obj) if (edit_mode and edit_obj) else None

    return render(request, "kakeibo/manage_fixed_list.html", {
        "title": "固定費（管理）",
        "owner": owner,
        "rows": qs,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "form": form,
    })