# =========================================
# [FILE] forms.py
# [PATH] kakeibo/forms.py
#
# このファイルは何？
# 家計簿の入力フォーム（支出/収入/設定管理）をまとめたファイル。
# - Expense/Incomeでカテゴリ候補を自動で絞る（支出カテゴリ/収入カテゴリ）
# - 設定画面用：CategoryForm / AccountForm（口座・カード）
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
        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})

        # 支出カテゴリのみ
        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")

        # 支出では口座/カードどちらも選べる（必要なら後で制限可能）
        self.fields["account"].queryset = Account.objects.all().order_by("kind", "id")


class IncomeForm(TransactionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["type"].initial = "INCOME"
        self.fields["type"].disabled = True
        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})

        # 収入カテゴリのみ
        self.fields["category"].queryset = Category.objects.filter(type="INCOME").order_by("order", "id")

        # 収入は基本「口座」寄り（カードに入金するケースもあるので全許可）
        self.fields["account"].queryset = Account.objects.all().order_by("kind", "id")


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