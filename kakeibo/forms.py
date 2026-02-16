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
# ★重要（今回の修正）
# iPhoneの <input type="month"> は "YYYY-MM" を送るが、
# DjangoのDateFieldは通常 "YYYY-MM-DD" を期待するためバリデーションで落ちる。
# → フォーム側で month を上書きし、input_formats=["%Y-%m"] を許可。
#   受け取ったら必ず day=1 に揃えて保存する。
#
# ★重要（今回の修正②）
# 変動費（カード）と銀行残高（口座）は owner によって候補を絞るが、
# queryset を Account.objects.none() のままだと GET表示で候補が0件になり、
# JSが動かなかった場合に「カードが出ない」事故になる。
# → GETでも最低限 “全候補” を出す（kindで絞る）。
#   POST時（self.dataがある時）と編集時（self.instanceがある時）は、ownerで絞る。
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
    # ✅ month を上書き（YYYY-MM を受け取れるようにする）
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
    # ✅ month を上書き（YYYY-MM を受け取れるようにする）
    month = MonthField(label="対象月", widget=MonthInput())

    class Meta:
        model = MonthlyVariableExpense
        fields = ["month", "owner", "var_type", "category", "card", "amount", "memo"]
        widgets = {
            "memo": forms.TextInput(attrs={"placeholder": "例：楽天カード 2月分 / 子供用品立替 など"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # ✅ category はユーザー入力不要：完全自動なので非表示＆入力要求もしない
        self.fields["category"].required = False
        self.fields["category"].widget = forms.HiddenInput()
        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")

        # ✅ 重要：GETでも最低限候補が出るようにする（JSが死んでも使える）
        self.fields["card"].queryset = Account.objects.filter(kind="CARD").order_by("owner", "id")

        # ★POST時：owner に応じて候補を絞る（バリデーションも通る）
        if self.data:
            owner = (self.data.get("owner") or "").strip()
            if owner:
                self.fields["card"].queryset = Account.objects.filter(kind="CARD", owner=owner).order_by("owner", "id")

        # ★編集時：既存ownerに合わせて絞る
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

        # ✅ var_type に応じて card 必須/不要
        if var_type == "CARD":
            if not card:
                self.add_error("card", "カードを選択してね。")
            else:
                # サーバ側でも安全にチェック（owner一致 & kind=CARD）
                if card.kind != "CARD" or (owner and card.owner != owner):
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
        ("HOUSE", "家"),
        ("B", "ぼーや"),
        ("G", "ごろ"),
    ]

    # ✅ month を上書き（YYYY-MM を受け取れるようにする）
    month = MonthField(label="対象月", widget=MonthInput())

    # ✅ 画面用：ownerを選ぶ（BankBalanceには保存しない）
    owner = forms.ChoiceField(label="所有者", choices=OWNER_CHOICES, required=True)

    class Meta:
        model = BankBalance
        fields = ["month", "owner", "account", "balance"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # ✅ 重要：GETでも最低限候補が出るようにする（JSが死んでも使える）
        self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT").order_by("owner", "id")

        # ★POST時：owner に応じて候補を絞る
        if self.data:
            owner = (self.data.get("owner") or "").strip()
            if owner:
                self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT", owner=owner).order_by("owner", "id")

        # ★編集時：既存レコードの口座ownerを初期表示＆候補復元
        elif self.instance and getattr(self.instance, "pk", None):
            try:
                self.fields["owner"].initial = self.instance.account.owner
                self.fields["account"].queryset = Account.objects.filter(
                    kind="ACCOUNT",
                    owner=self.instance.account.owner
                ).order_by("owner", "id")
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