# =========================================
# [FILE] expense.py
# [PATH] kakeibo/views/expense.py
#
# このファイルは何？
# 支出（/kakeibo/expense/）
# - 入口で「固定費 / 変動費」を選ぶ
# - 固定費：テンプレ（毎月同じ）
# - 変動費：月次（カード/立替/年金・保険/その他）
#
# ★重要
# POSTでフォームが invalid のとき、エラー付きフォームを捨てずにそのまま画面へ返す
#
# ★今回の修正ポイント
# - 変動費のトースト表示で存在しない item_category を参照していたので、
#   実在する category を表示する（保存エラーとは別だが、表示崩れを防ぐ）
# - 変動費の新規登録で、前回入力した
#   「対象月 / 誰の支出 / 種類」
#   を session に保存し、次回表示時の初期値として復元する
# =========================================

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404

from .permissions import kakeibo_access_required
from ..forms import FixedExpenseTemplateForm, MonthlyVariableExpenseForm
from ..models import FixedExpenseTemplate, MonthlyVariableExpense


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


def _variable_session_key_month() -> str:
    return "kakeibo_expense_variable_last_month"


def _variable_session_key_owner() -> str:
    return "kakeibo_expense_variable_last_owner"


def _variable_session_key_var_type() -> str:
    return "kakeibo_expense_variable_last_var_type"


def _save_variable_last_values(request, month, owner: str, var_type: str) -> None:
    """
    何をする？
    - 変動費の連続入力を楽にするため、
      前回の「対象月 / 誰の支出 / 種類」を session に保存する
    """
    if month:
        try:
            request.session[_variable_session_key_month()] = month.strftime("%Y-%m")
        except Exception:
            request.session[_variable_session_key_month()] = str(month)

    if owner:
        request.session[_variable_session_key_owner()] = owner

    if var_type:
        request.session[_variable_session_key_var_type()] = var_type


def _variable_initial_from_session(request) -> dict:
    """
    何をする？
    - session に保存した前回の入力値から、
      変動費フォームの initial を作る
    """
    initial = {}

    month = (request.session.get(_variable_session_key_month()) or "").strip()
    owner = (request.session.get(_variable_session_key_owner()) or "").strip()
    var_type = (request.session.get(_variable_session_key_var_type()) or "").strip()

    if month:
        initial["month"] = month
    if owner:
        initial["owner"] = owner
    if var_type:
        initial["var_type"] = var_type

    return initial


@login_required
def expense(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    return render(request, "kakeibo/expense_menu.html", {
        "title": "支出",
    })


@login_required
def expense_fixed(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(FixedExpenseTemplate, id=edit_id)
        edit_mode = True

    form = FixedExpenseTemplateForm(instance=edit_obj) if (edit_mode and edit_obj) else FixedExpenseTemplateForm()

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "create":
            form = FixedExpenseTemplateForm(request.POST)
            if form.is_valid():
                obj = form.save()

                owner = getattr(obj, "owner", "")
                category = getattr(getattr(obj, "category", None), "name", "")
                amount = getattr(obj, "amount", None)
                messages.success(request, f"✅ 固定費を登録：{_owner_label(owner)} / {category} / ¥{_yen(amount)}")

                return redirect("/kakeibo/expense/fixed/")

        elif action == "update":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))
            edit_obj = obj
            edit_mode = True
            form = FixedExpenseTemplateForm(request.POST, instance=obj)
            if form.is_valid():
                x = form.save()

                owner = getattr(x, "owner", "")
                category = getattr(getattr(x, "category", None), "name", "")
                amount = getattr(x, "amount", None)
                messages.success(request, f"✅ 固定費を更新：{_owner_label(owner)} / {category} / ¥{_yen(amount)}")

                return redirect("/kakeibo/expense/fixed/")

        elif action == "delete":
            obj = get_object_or_404(FixedExpenseTemplate, id=request.POST.get("id"))

            owner = getattr(obj, "owner", "")
            category = getattr(getattr(obj, "category", None), "name", "")
            amount = getattr(obj, "amount", None)

            obj.delete()
            messages.success(request, f"✅ 固定費を削除：{_owner_label(owner)} / {category} / ¥{_yen(amount)}")

            return redirect("/kakeibo/expense/fixed/")

    rows = FixedExpenseTemplate.objects.order_by("-is_active", "id")

    return render(request, "kakeibo/manage_list.html", {
        "title": "固定費",
        "form": form,
        "rows": rows,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "tab_key": "fixed",
    })


@login_required
def expense_variable(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(MonthlyVariableExpense, id=edit_id)
        edit_mode = True

    if edit_mode and edit_obj:
        form = MonthlyVariableExpenseForm(instance=edit_obj)
    else:
        form = MonthlyVariableExpenseForm(initial=_variable_initial_from_session(request))

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "create":
            form = MonthlyVariableExpenseForm(request.POST)
            if form.is_valid():
                obj = form.save()

                month = getattr(obj, "month", None)
                owner = getattr(obj, "owner", "")
                var_type = getattr(obj, "var_type", "")
                card_name = getattr(getattr(obj, "card", None), "name", "")
                category_name = getattr(getattr(obj, "category", None), "name", "")
                amount = getattr(obj, "amount", None)

                _save_variable_last_values(
                    request=request,
                    month=month,
                    owner=owner,
                    var_type=var_type,
                )

                month_s = month.strftime("%Y-%m") if month else ""
                vt = "カード" if var_type == "CARD" else ("立替" if var_type == "ADVANCE" else ("年金・保険" if var_type == "PENSION" else ("その他" if var_type == "OTHER" else var_type)))
                head = f"{month_s} / {_owner_label(owner)} / {vt} / {category_name}"
                tail = f" / {card_name}" if (var_type == "CARD" and card_name) else ""
                messages.success(request, f"✅ 変動費を登録：{head}{tail} / ¥{_yen(amount)}")

                return redirect("/kakeibo/expense/variable/")

        elif action == "update":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))
            edit_obj = obj
            edit_mode = True
            form = MonthlyVariableExpenseForm(request.POST, instance=obj)
            if form.is_valid():
                x = form.save()

                month = getattr(x, "month", None)
                owner = getattr(x, "owner", "")
                var_type = getattr(x, "var_type", "")
                card_name = getattr(getattr(x, "card", None), "name", "")
                category_name = getattr(getattr(x, "category", None), "name", "")
                amount = getattr(x, "amount", None)

                _save_variable_last_values(
                    request=request,
                    month=month,
                    owner=owner,
                    var_type=var_type,
                )

                month_s = month.strftime("%Y-%m") if month else ""
                vt = "カード" if var_type == "CARD" else ("立替" if var_type == "ADVANCE" else ("年金・保険" if var_type == "PENSION" else ("その他" if var_type == "OTHER" else var_type)))
                head = f"{month_s} / {_owner_label(owner)} / {vt} / {category_name}"
                tail = f" / {card_name}" if (var_type == "CARD" and card_name) else ""
                messages.success(request, f"✅ 変動費を更新：{head}{tail} / ¥{_yen(amount)}")

                return redirect("/kakeibo/expense/variable/")

        elif action == "delete":
            obj = get_object_or_404(MonthlyVariableExpense, id=request.POST.get("id"))

            month = getattr(obj, "month", None)
            owner = getattr(obj, "owner", "")
            var_type = getattr(obj, "var_type", "")
            card_name = getattr(getattr(obj, "card", None), "name", "")
            category_name = getattr(getattr(obj, "category", None), "name", "")
            amount = getattr(obj, "amount", None)

            month_s = month.strftime("%Y-%m") if month else ""
            vt = "カード" if var_type == "CARD" else ("立替" if var_type == "ADVANCE" else ("年金・保険" if var_type == "PENSION" else ("その他" if var_type == "OTHER" else var_type)))
            head = f"{month_s} / {_owner_label(owner)} / {vt} / {category_name}"
            tail = f" / {card_name}" if (var_type == "CARD" and card_name) else ""

            obj.delete()
            messages.success(request, f"✅ 変動費を削除：{head}{tail} / ¥{_yen(amount)}")

            return redirect("/kakeibo/expense/variable/")

    rows = MonthlyVariableExpense.objects.order_by("-month", "-id")[:200]

    return render(request, "kakeibo/manage_list.html", {
        "title": "変動費",
        "form": form,
        "rows": rows,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
        "tab_key": "variable",
    })