# =========================================
# [FILE] forms.py
# [PATH] kakeibo/forms.py
#
# このファイルは何？
# 家計簿のフォーム定義（B案：月次方式）。
# - 設定（カテゴリ/口座/カード）
# - 収入（月次）
# - 支出：固定費テンプレ / 変動費（月次：カード/立替）
# - 銀行残高（月次）
#
# 重要仕様：
# - Category.code はユーザーに入力させない（自動）
# - 変動費は var_type に応じて category(code=CARD/ADVANCE) を自動セット
# =========================================

from django import forms
from django.utils import timezone

from .models import (
    Account,
    Category,
    MonthlyIncome,
    FixedExpenseTemplate,
    MonthlyVariableExpense,
    BankBalance,
)


def normalize_month(d):
    # month は DateFieldだけど、必ず day=1 に揃える
    return d.replace(day=1)


class MonthInput(forms.DateInput):
    input_type = "month"


# ----------------------------
# 設定
# ----------------------------
class CategoryForm(forms.ModelForm):
    """
    Category.code はUIで触らせない方針。
    必要な code（CARD/ADVANCE）は settings_view 側で自動セットする。
    """
    class Meta:
        model = Category
        fields = ["name", "order"]  # ✅ code は触らせない
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "例：食費 / 給与 / お小遣い / カード / 立替 など"}),
            "order": forms.NumberInput(attrs={"inputmode": "numeric"}),
        }


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["owner", "name"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "例：○○銀行 / 現金 / 楽天カード"}),
        }


# ----------------------------
# 収入（月次）
# ----------------------------
class MonthlyIncomeForm(forms.ModelForm):
    class Meta:
        model = MonthlyIncome
        fields = ["month", "owner", "category", "amount", "memo"]
        widgets = {
            "month": MonthInput(),
            "memo": forms.TextInput(attrs={"placeholder": "例：2月 給与 / 副業 など"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        self.fields["category"].queryset = Category.objects.filter(type="INCOME").order_by("order", "id")
        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})

    def clean_month(self):
        m = self.cleaned_data["month"]
        return normalize_month(m)


# ----------------------------
# 固定費テンプレ
# ----------------------------
class FixedExpenseTemplateForm(forms.ModelForm):
    class Meta:
        model = FixedExpenseTemplate
        fields = ["owner", "category", "amount", "memo", "is_active"]
        widgets = {
            "memo": forms.TextInput(attrs={"placeholder": "例：家賃 / 保険 / サブスク など"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")
        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})


# ----------------------------
# 変動費（月次：カード/立替）
# ----------------------------
class MonthlyVariableExpenseForm(forms.ModelForm):
    """
    変動費は「カード」or「立替」だけ。
    - var_type=CARD   → category.code=CARD を自動セット、card 必須
    - var_type=ADVANCE→ category.code=ADVANCE を自動セット、card 不要
    """
    class Meta:
        model = MonthlyVariableExpense
        fields = ["month", "owner", "var_type", "card", "amount", "memo"]  # ✅ categoryはUIに出さない
        widgets = {
            "month": MonthInput(),
            "memo": forms.TextInput(attrs={"placeholder": "例：楽天カード 2月分 / 子供用品立替 など"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # カードは kind=CARD のみ
        self.fields["card"].queryset = Account.objects.filter(kind="CARD").order_by("owner", "id")
        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})

        # 初期表示：カード欄は var_type=CARD のときだけ見せたい（JSで切替）
        # ここではフォーム自体は常に持つ（JSが隠す）

    def clean_month(self):
        m = self.cleaned_data["month"]
        return normalize_month(m)

    def clean(self):
        cleaned = super().clean()
        var_type = cleaned.get("var_type")
        card = cleaned.get("card")

        # ✅ var_type に応じて card 必須/不要を強制
        if var_type == "CARD":
            if not card:
                self.add_error("card", "カードを選択してね。")
        elif var_type == "ADVANCE":
            cleaned["card"] = None
        else:
            # 未選択時
            return cleaned

        # ✅ category(code) を自動解決（無ければエラー）
        need_code = var_type  # "CARD" or "ADVANCE"
        q = Category.objects.filter(type="EXPENSE", code=need_code)
        if q.exists():
            self._resolved_category = q.first()
        else:
            raise forms.ValidationError(
                f"設定で『支出カテゴリ』に {need_code} を自動作成できていません。"
                f"（この後の設定画面の自動化で直ります）"
            )

        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        # clean() で解決した category を注入
        if hasattr(self, "_resolved_category") and self._resolved_category:
            obj.category = self._resolved_category
        if commit:
            obj.save()
        return obj


# ----------------------------
# 銀行残高（月次）
# ----------------------------
class BankBalanceForm(forms.ModelForm):
    class Meta:
        model = BankBalance
        fields = ["month", "account", "balance"]
        widgets = {
            "month": MonthInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # 口座のみ（kind=ACCOUNT）
        self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT").order_by("owner", "id")
        self.fields["balance"].widget.attrs.update({"inputmode": "numeric"})

    def clean_month(self):
        m = self.cleaned_data["month"]
        return normalize_month(m)