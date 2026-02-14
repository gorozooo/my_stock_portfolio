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
    class Meta:
        model = Category
        fields = ["name", "code", "order"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "例：食費 / 給与 / お小遣い など"}),
            "code": forms.TextInput(attrs={"placeholder": "例：CARD / ADVANCE（必要な時だけ）"}),
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
    class Meta:
        model = MonthlyVariableExpense
        fields = ["month", "owner", "var_type", "category", "card", "amount", "memo"]
        widgets = {
            "month": MonthInput(),
            "memo": forms.TextInput(attrs={"placeholder": "例：楽天カード 2月分 / 子供用品立替 など"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # 変動費カテゴリは UI上見せてもいいが、実際は code で固定化する
        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")

        # カードは kind=CARD のみ
        self.fields["card"].queryset = Account.objects.filter(kind="CARD").order_by("owner", "id")

        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})

    def clean_month(self):
        m = self.cleaned_data["month"]
        return normalize_month(m)

    def clean(self):
        cleaned = super().clean()
        var_type = cleaned.get("var_type")
        card = cleaned.get("card")
        category = cleaned.get("category")

        # ✅ var_type に応じて card 必須/不要を強制
        if var_type == "CARD":
            if not card:
                self.add_error("card", "カードを選択してね。")
        else:
            # ADVANCE はカード不要
            cleaned["card"] = None

        # ✅ category は code で強制（表示名を変えても壊れない）
        #   - CARD のとき → code=CARD を使う
        #   - ADVANCE のとき → code=ADVANCE を使う
        if var_type in ("CARD", "ADVANCE"):
            need_code = var_type
            q = Category.objects.filter(type="EXPENSE", code=need_code)
            if q.exists():
                cleaned["category"] = q.first()
            else:
                # 設定で作ってない場合はエラーで止める（勝手に作らない）
                self.add_error("category", f"設定で code={need_code} の支出カテゴリを1つ作ってください。")

        return cleaned


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