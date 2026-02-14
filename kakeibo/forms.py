# =========================================
# [FILE] forms.py
# [PATH] kakeibo/forms.py
#
# このファイルは何？
# 家計簿のフォーム定義。
# - B案（月次方式）へ移行したため、Transactionベースのフォームは廃止
# - いまは「設定（カテゴリ/口座/カード）」のフォームだけを残す
# =========================================

from django import forms
from .models import Account, Category


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
            "name": forms.TextInput(attrs={"placeholder": "例：楽天カード / 現金 / ○○銀行"}),
        }