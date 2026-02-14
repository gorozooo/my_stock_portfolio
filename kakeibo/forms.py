# =========================================
# [FILE] forms.py
# [PATH] kakeibo/forms.py
#
# このファイルは何？
# 家計簿の入力フォーム（支出/収入/設定）をまとめたファイル。
# - 収入は収入カテゴリだけ、支出は支出カテゴリだけ表示する
# - 設定画面は CategoryForm / AccountForm を使う
# =========================================

from django import forms
from .models import Account, Category, Transaction


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

        # ✅ 支出カテゴリだけ出す
        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")

        # ✅ 支出は「口座/カード」両方出してOK（必要なら将来フィルタ）
        self.fields["account"].queryset = Account.objects.all().order_by("kind", "id")

        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})


class IncomeForm(TransactionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["type"].initial = "INCOME"
        self.fields["type"].disabled = True

        # ✅ 収入カテゴリだけ出す
        self.fields["category"].queryset = Category.objects.filter(type="INCOME").order_by("order", "id")

        self.fields["account"].queryset = Account.objects.all().order_by("kind", "id")

        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "order"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "例：食費 / 交際費 / 給与 など"}),
            "order": forms.NumberInput(attrs={"inputmode": "numeric"}),
        }


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "例：楽天カード / 現金 / 銀行"}),
        }