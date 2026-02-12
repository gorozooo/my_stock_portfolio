# =========================================
# [FILE] forms.py
# [PATH] kakeibo/forms.py
#
# このファイルは何？
# 家計簿の入力フォーム（支出/収入）をまとめたファイル。
# Viewは「画面表示と保存」に集中し、フォームはここで管理して肥大化を防ぐ。
# =========================================

from django import forms
from .models import Account, Transaction


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ["type", "account", "category", "paid_by", "amount", "date", "memo"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "memo": forms.TextInput(attrs={"placeholder": "例：楽天カード 2月分 / 子供用品立替"}),
        }


class ExpenseForm(TransactionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["type"].initial = "EXPENSE"
        self.fields["type"].disabled = True

        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})


class IncomeForm(TransactionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["type"].initial = "INCOME"
        self.fields["type"].disabled = True

        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "例：楽天カード / 現金 / 銀行"}),
        }