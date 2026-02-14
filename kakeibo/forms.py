# =========================================
# [FILE] forms.py
# [PATH] kakeibo/forms.py
#
# このファイルは何？
# 家計簿のフォーム定義（B案：月次方式）。
# - 設定（カテゴリ/口座/カード）
# - 収入（月次）
# - 支出：固定費テンプレ / 変動費（月次：カード/立替）
# - 銀行残高（月次） ※ownerを画面で選び、口座候補をownerで絞る
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

        # 表示上は出すが、実際は code で自動強制される
        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")

        # ✅ 重要：カード候補は最初は空にする（owner選択後にJSで入れる）
        self.fields["card"].queryset = Account.objects.none()

        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})

    def clean_month(self):
        m = self.cleaned_data["month"]
        return normalize_month(m)

    def clean(self):
        cleaned = super().clean()
        var_type = cleaned.get("var_type")
        card = cleaned.get("card")
        owner = cleaned.get("owner")

        # ✅ var_type に応じて card 必須/不要
        if var_type == "CARD":
            if not card:
                self.add_error("card", "カードを選択してね。")
            else:
                # サーバ側でも安全にチェック（owner一致 & kind=CARD）
                if card.kind != "CARD" or card.owner != owner:
                    self.add_error("card", "選んだカードが「誰の支出」と一致していません。")
        else:
            cleaned["card"] = None

        # ✅ category は code で強制（CARD/ADVANCE）
        if var_type in ("CARD", "ADVANCE"):
            need_code = var_type
            q = Category.objects.filter(type="EXPENSE", code=need_code)
            if q.exists():
                cleaned["category"] = q.first()
            else:
                self.add_error("category", f"設定で code={need_code} の支出カテゴリを1つ作ってください。")

        return cleaned


# ----------------------------
# 銀行残高（月次）
# ----------------------------
class BankBalanceForm(forms.ModelForm):
    OWNER_CHOICES = [
        ("HOUSE", "家計"),
        ("B", "B（夫）"),
        ("G", "G（妻）"),
    ]

    # ✅ 画面用：ownerを選ぶ（BankBalanceには保存しない）
    owner = forms.ChoiceField(label="所有者", choices=OWNER_CHOICES, required=True)

    class Meta:
        model = BankBalance
        fields = ["month", "owner", "account", "balance"]
        widgets = {
            "month": MonthInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # ✅ 重要：口座候補は最初は空（owner選択後にJSで入れる）
        self.fields["account"].queryset = Account.objects.none()

        self.fields["balance"].widget.attrs.update({"inputmode": "numeric"})

        # 編集時：既存レコードの口座ownerを初期表示に反映
        if self.instance and getattr(self.instance, "pk", None):
            try:
                self.fields["owner"].initial = self.instance.account.owner
            except Exception:
                pass

    def clean_month(self):
        m = self.cleaned_data["month"]
        return normalize_month(m)

    def clean(self):
        cleaned = super().clean()
        owner = cleaned.get("owner")
        account = cleaned.get("account")

        if account:
            if account.kind != "ACCOUNT":
                self.add_error("account", "口座を選んでください（カードは不可）。")
            if owner and account.owner != owner:
                self.add_error("account", "選んだ口座が「所有者」と一致していません。")

        return cleaned