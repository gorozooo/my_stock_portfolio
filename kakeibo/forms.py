# =========================================
# [FILE] forms.py
# [PATH] kakeibo/forms.py
#
# このファイルは何？
# 家計簿のフォーム定義（B案：月次方式）。
# - 設定（カテゴリ/口座/カード）
# - 収入（月次）
# - 支出：固定費テンプレ / 変動費（月次：カード/立替/年金・保険/その他）
# - 銀行残高（月次）
#
# 重要：
# - iPhoneの <input type="month"> は "YYYY-MM" を送るため、
#   MonthField で "YYYY-MM" を受け取り、必ず day=1 に揃える。
#
# 今回の修正：
# - BankBalanceForm で「ごろ × 千葉銀行」のように同名口座が複数ある場合でも、
#   owner と account がズレて送信されたら、同じ口座名・正しいownerの口座へ自動補正する。
# - BankBalanceForm の初期表示でも、owner が初期値にある場合は account 候補をその owner に絞る。
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
    month = MonthField(label="対象月", widget=MonthInput())

    class Meta:
        model = MonthlyIncome
        fields = ["month", "owner", "category", "amount", "memo"]
        widgets = {
            "memo": forms.TextInput(attrs={"placeholder": "例： お年玉/ 副業 など"}),
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
            "memo": forms.TextInput(attrs={"placeholder": "例：保険 / プルデンシャル / サブスク など"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")
        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})


# ----------------------------
# 変動費（月次：カード/立替/年金・保険/その他）
# ----------------------------
class MonthlyVariableExpenseForm(forms.ModelForm):
    month = MonthField(label="対象月", widget=MonthInput())

    class Meta:
        model = MonthlyVariableExpense
        fields = ["month", "owner", "var_type", "category", "card", "amount", "memo"]
        widgets = {
            "memo": forms.TextInput(attrs={"placeholder": "例：コンタクト / 子供用品 など"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        self.fields["category"].required = False
        self.fields["category"].widget = forms.HiddenInput()
        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")

        self.fields["card"].queryset = Account.objects.filter(kind="CARD").order_by("owner", "id")

        if self.data:
            owner = (self.data.get("owner") or "").strip()
            if owner:
                self.fields["card"].queryset = Account.objects.filter(kind="CARD", owner=owner).order_by("owner", "id")

        elif self.instance and getattr(self.instance, "pk", None):
            inst_owner = getattr(self.instance, "owner", None)
            if inst_owner:
                self.fields["card"].queryset = Account.objects.filter(kind="CARD", owner=inst_owner).order_by("owner", "id")

        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})

    def clean(self):
        cleaned = super().clean()
        var_type = cleaned.get("var_type")
        card = cleaned.get("card")
        owner = cleaned.get("owner")

        if var_type == "CARD":
            if not card:
                self.add_error("card", "カードを選択してね。")
            else:
                if card.kind != "CARD" or (owner and card.owner != owner):
                    self.add_error("card", "選んだカードが「誰の支出」と一致していません。")
        else:
            cleaned["card"] = None

        if var_type:
            q = Category.objects.filter(type="EXPENSE", code=var_type)
            if q.exists():
                cleaned["category"] = q.first()
            else:
                self.add_error("category", f"設定で code={var_type} の支出カテゴリを1つ作ってください。")

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

    # 画面用：ownerを選ぶ（BankBalanceには保存しない）
    owner = forms.ChoiceField(label="所有者", choices=OWNER_CHOICES, required=True)

    class Meta:
        model = BankBalance
        fields = ["month", "owner", "account", "balance"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # GETでも最低限候補が出るようにする
        self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT").order_by("owner", "id")

        # POST時：owner に応じて候補を絞る
        if self.data:
            owner = (self.data.get("owner") or "").strip()
            if owner:
                # 重要：
                # 通常は owner で絞る。
                # ただし clean() で「同名口座の自動補正」をするため、
                # 送信された account_id も queryset に含めておく。
                account_id = (self.data.get("account") or "").strip()

                qs = Account.objects.filter(kind="ACCOUNT", owner=owner)

                if account_id:
                    try:
                        submitted_qs = Account.objects.filter(kind="ACCOUNT", id=int(account_id))
                        qs = qs | submitted_qs
                    except Exception:
                        pass

                self.fields["account"].queryset = qs.distinct().order_by("owner", "id")

        # 編集時：既存レコードの口座ownerを初期表示＆候補復元
        elif self.instance and getattr(self.instance, "pk", None):
            try:
                self.fields["owner"].initial = self.instance.account.owner
                self.fields["account"].queryset = Account.objects.filter(
                    kind="ACCOUNT",
                    owner=self.instance.account.owner
                ).order_by("owner", "id")
            except Exception:
                self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT").order_by("owner", "id")

        # 新規GET時：initial に owner があれば、その所有者の口座に絞る
        else:
            initial_owner = None

            try:
                initial_owner = self.initial.get("owner")
            except Exception:
                initial_owner = None

            if initial_owner:
                self.fields["account"].queryset = Account.objects.filter(
                    kind="ACCOUNT",
                    owner=initial_owner
                ).order_by("owner", "id")

        self.fields["balance"].widget.attrs.update({"inputmode": "numeric"})

    def clean(self):
        cleaned = super().clean()
        owner = cleaned.get("owner")
        account = cleaned.get("account")

        if account:
            if account.kind != "ACCOUNT":
                self.add_error("account", "口座を選んでください（カードは不可）。")
                return cleaned

            # ここが今回の重要修正
            # 例：
            # owner=G なのに account=Bの千葉銀行 が送られてきた場合、
            # owner=G かつ name=千葉銀行 の口座があれば、そちらへ自動補正する。
            if owner and account.owner != owner:
                replacement = (
                    Account.objects
                    .filter(kind="ACCOUNT", owner=owner, name=account.name)
                    .order_by("id")
                    .first()
                )

                if replacement:
                    cleaned["account"] = replacement
                else:
                    self.add_error("account", "選んだ口座が「所有者」と一致していません。")

        return cleaned