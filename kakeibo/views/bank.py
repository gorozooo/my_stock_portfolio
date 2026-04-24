# =========================================
# [FILE] bank.py
# [PATH] kakeibo/views/bank.py
#
# このファイルは何？
# 銀行（/kakeibo/bank/）
# - 月次で「口座残高」を手入力する（入力のみ）
# - 口座は設定タブの「口座」で追加（owner=B/G/HOUSE）
# - 入力画面に登録済み一覧は出さない（管理で探す）
#
# 今回の修正：
# - 前回入力した 対象月 / 所有者 / 口座 を session に保存し、次回入力時に復元する
# - フォームが invalid の時も、何も起きていないように見えないようエラートーストを出す
# - 同名口座（例：ぼーや千葉銀行 / ごろ千葉銀行）がある場合でも forms.py 側の補正で保存できるようにする
# =========================================

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone

from .permissions import kakeibo_access_required
from ..forms import BankBalanceForm
from ..models import BankBalance, Account


SESSION_BANK_MONTH = "kakeibo_bank_last_month"
SESSION_BANK_OWNER = "kakeibo_bank_last_owner"
SESSION_BANK_ACCOUNT = "kakeibo_bank_last_account"


def _yen(amount) -> str:
    if amount is None:
        return ""
    try:
        return f"{int(amount):,}"
    except Exception:
        return f"{amount}"


def _month_value(d) -> str:
    try:
        return d.strftime("%Y-%m")
    except Exception:
        return ""


def _save_last_input_to_session(request, month, owner, account):
    """
    何をする？
    - 銀行残高入力の前回値を session に保存する。
    - 次回、同じ月・所有者・口座を初期表示するため。
    """
    try:
        if month:
            request.session[SESSION_BANK_MONTH] = _month_value(month)
        if owner:
            request.session[SESSION_BANK_OWNER] = owner
        if account:
            request.session[SESSION_BANK_ACCOUNT] = int(account.id)
    except Exception:
        pass


def _initial_from_session(request) -> dict:
    """
    何をする？
    - 銀行残高入力の初期値を作る。
    - session に前回値があればそれを優先。
    - なければ当月の1日を使う。
    """
    today_month = timezone.localdate().replace(day=1)

    initial = {
        "month": today_month,
    }

    # 対象月
    month_s = (request.session.get(SESSION_BANK_MONTH) or "").strip()
    if month_s:
        try:
            y, m = month_s.split("-")
            initial["month"] = today_month.replace(year=int(y), month=int(m), day=1)
        except Exception:
            initial["month"] = today_month

    # 所有者
    owner = (request.session.get(SESSION_BANK_OWNER) or "").strip()
    if owner:
        initial["owner"] = owner

    # 口座
    account_id = request.session.get(SESSION_BANK_ACCOUNT)
    if account_id:
        try:
            acc = Account.objects.filter(id=account_id, kind="ACCOUNT").first()
            if acc:
                initial["account"] = acc.id
                if not owner:
                    initial["owner"] = acc.owner
        except Exception:
            pass

    return initial


def _form_error_text(form) -> str:
    """
    何をする？
    - フォームエラーをトースト用に短くまとめる。
    """
    try:
        parts = []
        for field, errors in form.errors.items():
            label = form.fields.get(field).label if field in form.fields else field
            for e in errors:
                parts.append(f"{label}: {e}")
        return " / ".join(parts)
    except Exception:
        return "入力内容を確認してください。"


@login_required
def bank(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    edit_id = request.GET.get("edit")
    edit_mode = False
    edit_obj = None

    if edit_id:
        edit_obj = get_object_or_404(BankBalance, id=edit_id)
        edit_mode = True

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "create":
            form = BankBalanceForm(request.POST)
            if form.is_valid():
                m = form.cleaned_data["month"]
                acc = form.cleaned_data["account"]
                bal = form.cleaned_data["balance"]
                owner = form.cleaned_data.get("owner") or getattr(acc, "owner", "")

                obj, created = BankBalance.objects.get_or_create(
                    month=m,
                    account=acc,
                    defaults={"balance": bal},
                )

                month_s = m.strftime("%Y-%m") if m else ""
                acc_name = getattr(acc, "name", "")

                if not created and obj.balance != bal:
                    obj.balance = bal
                    obj.save()
                    messages.success(request, f"✅ 銀行残高を更新：{month_s} / {acc_name} / ¥{_yen(bal)}")
                elif created:
                    messages.success(request, f"✅ 銀行残高を登録：{month_s} / {acc_name} / ¥{_yen(bal)}")
                else:
                    messages.success(request, f"✅ 銀行残高：変更なし（{month_s} / {acc_name}）")

                _save_last_input_to_session(request, m, owner, acc)

                return redirect("/kakeibo/bank/")

            messages.error(request, f"⚠️ 銀行残高を登録できませんでした：{_form_error_text(form)}")

        elif action == "update":
            obj = get_object_or_404(BankBalance, id=request.POST.get("id"))
            form = BankBalanceForm(request.POST, instance=obj)
            if form.is_valid():
                x = form.save()

                m = getattr(x, "month", None)
                acc = getattr(x, "account", None)
                bal = getattr(x, "balance", None)
                owner = form.cleaned_data.get("owner") or getattr(acc, "owner", "")

                month_s = m.strftime("%Y-%m") if m else ""
                acc_name = getattr(acc, "name", "")

                messages.success(request, f"✅ 銀行残高を更新：{month_s} / {acc_name} / ¥{_yen(bal)}")

                _save_last_input_to_session(request, m, owner, acc)

                return redirect("/kakeibo/bank/")

            edit_mode = True
            edit_obj = obj
            messages.error(request, f"⚠️ 銀行残高を更新できませんでした：{_form_error_text(form)}")

        elif action == "delete":
            # 入力画面では削除しない方針（管理でやる）
            return redirect("/kakeibo/bank/")

    else:
        if edit_mode and edit_obj:
            form = BankBalanceForm(instance=edit_obj)
        else:
            form = BankBalanceForm(initial=_initial_from_session(request))

    return render(request, "kakeibo/bank.html", {
        "title": "銀行残高",
        "form": form,
        "edit_mode": edit_mode,
        "edit_obj": edit_obj,
    })