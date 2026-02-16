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
# ★重要（iPhone month対策）
# <input type="month"> は "YYYY-MM" を送るが DateField は "YYYY-MM-DD" を期待しがち
# → MonthFieldで "%Y-%m" を許可し、day=1固定で保存する
#
# ★重要（owner絞り込み）
# card/account を owner で絞るとき、queryset=none のままだと POST バリデーションで弾かれる
# → POST時に owner を見て queryset を復元する
#
# ★今回（理想形）
# 変動費：
# - category は内部用（code=CARD / ADVANCE）を自動セットして非表示
# - item_category を新設し、ユーザーが「年金・保険」「その他」等の“支出項目”を選ぶ
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
    return d.replace(day=1)


class MonthInput(forms.DateInput):
    input_type = "month"


class MonthField(forms.DateField):
    """
    このフィールドは何？
    - <input type="month"> が送る "YYYY-MM" を受け取るためのDateField。
    - DateFieldとしてパースした後、day=1に揃える。
    """
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("input_formats", ["%Y-%m", "%Y-%m-%d"])
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        d = super().to_python(value)
        if d is None:
            return None
        return normalize_month(d)


# ----------------------------
# 設定
# ----------------------------
class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "code", "order"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "例：食費 / 給与 / 年金・保険 / その他 など"}),
            "code": forms.TextInput(attrs={"placeholder": "例：CARD / ADVANCE（内部用：支払い分類に使う）"}),
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
    month = MonthField(label="対象月", widget=MonthInput())

    class Meta:
        model = MonthlyIncome
        fields = ["month", "owner", "category", "amount", "memo"]
        widgets = {
            "memo": forms.TextInput(attrs={"placeholder": "例：2月 給与 / 副業 など"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        self.fields["category"].queryset = Category.objects.filter(type="INCOME").order_by("order", "id")
        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})


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
    month = MonthField(label="対象月", widget=MonthInput())

    class Meta:
        model = MonthlyVariableExpense
        fields = ["month", "owner", "var_type", "category", "item_category", "card", "amount", "memo"]
        widgets = {
            "memo": forms.TextInput(attrs={"placeholder": "例：2月分 / 子供用品 / メモ など"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # ✅ category は内部用：ユーザー入力不要
        self.fields["category"].required = False
        self.fields["category"].widget = forms.HiddenInput()
        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")

        # ✅ item_category はユーザーが選ぶ「支出項目」
        # 内部用の code=CARD/ADVANCE は“項目”としては邪魔なので除外
        self.fields["item_category"].queryset = (
            Category.objects.filter(type="EXPENSE")
            .exclude(code__in=["CARD", "ADVANCE"])
            .order_by("order", "id")
        )

        # ✅ カード候補は基本空（owner選択後にJS）
        self.fields["card"].queryset = Account.objects.none()

        # ★重要：POST時は owner に応じて queryset を復元
        if self.data:
            owner = self.data.get("owner")
            if owner:
                self.fields["card"].queryset = Account.objects.filter(kind="CARD", owner=owner).order_by("owner", "id")
            else:
                self.fields["card"].queryset = Account.objects.filter(kind="CARD").order_by("owner", "id")
        elif self.instance and getattr(self.instance, "pk", None):
            self.fields["card"].queryset = Account.objects.filter(kind="CARD", owner=self.instance.owner).order_by("owner", "id")

        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})

    def clean(self):
        cleaned = super().clean()
        var_type = cleaned.get("var_type")
        card = cleaned.get("card")
        owner = cleaned.get("owner")
        item_category = cleaned.get("item_category")

        # ✅ item_category は必須（支出項目）
        if not item_category:
            self.add_error("item_category", "項目を選択してね。")

        # ✅ var_type に応じて card 必須/不要
        if var_type == "CARD":
            if not card:
                self.add_error("card", "カードを選択してね。")
            else:
                if card.kind != "CARD" or (owner and card.owner != owner):
                    self.add_error("card", "選んだカードが「誰の支出」と一致していません。")
        else:
            cleaned["card"] = None

        # ✅ category（内部用）は code で強制（CARD/ADVANCE）
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
        ("HOUSE", "家"),
        ("B", "ぼーや"),
        ("G", "ごろ"),
    ]

    month = MonthField(label="対象月", widget=MonthInput())
    owner = forms.ChoiceField(label="所有者", choices=OWNER_CHOICES, required=True)

    class Meta:
        model = BankBalance
        fields = ["month", "owner", "account", "balance"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        self.fields["account"].queryset = Account.objects.none()

        if self.data:
            owner = self.data.get("owner")
            if owner:
                self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT", owner=owner).order_by("owner", "id")
            else:
                self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT").order_by("owner", "id")
        elif self.instance and getattr(self.instance, "pk", None):
            try:
                self.fields["owner"].initial = self.instance.account.owner
                self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT", owner=self.instance.account.owner).order_by("owner", "id")
            except Exception:
                self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT").order_by("owner", "id")

        self.fields["balance"].widget.attrs.update({"inputmode": "numeric"})

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