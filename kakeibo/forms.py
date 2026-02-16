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
# queryset を Account.objects.none() のままだと POST バリデーションで弾かれて保存できない。
# → POST時（self.dataがある時）に owner を見て queryset を復元する。
#   編集時（self.instanceがある時）も同様に復元する。
#
# ★重要（今回の修正③）
# 「管理（編集）」や「JSが効かない/順番がズレた」ケースでも落ちないように、
# owner が取れない場合は “全候補” で受け、最終チェックは clean() で厳密に行う。
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

    def _set_card_queryset_by_owner(self, owner: str | None):
        """
        何をする？
        - owner があれば、そのownerのカードだけに絞る
        - owner がなければ、全カードにする（POSTバリデーション落ち回避）
        """
        if owner:
            self.fields["card"].queryset = Account.objects.filter(kind="CARD", owner=owner).order_by("owner", "id")
        else:
            self.fields["card"].queryset = Account.objects.filter(kind="CARD").order_by("owner", "id")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # ✅ category はユーザー入力不要：完全自動なので非表示＆入力要求もしない
        self.fields["category"].required = False
        self.fields["category"].widget = forms.HiddenInput()
        self.fields["category"].queryset = Category.objects.filter(type="EXPENSE").order_by("order", "id")

        # ✅ 初期は空（JSが入れる想定）。ただし POST/編集では必ず復元する
        self.fields["card"].queryset = Account.objects.none()

        # --- queryset復元（重要）---
        if self.data:
            # POST（作成/更新）
            owner = (self.data.get("owner") or "").strip() or None
            if not owner and self.instance and getattr(self.instance, "pk", None):
                # ownerがPOSTに無い場合は、編集インスタンスから推定
                owner = getattr(self.instance, "owner", None) or None
            self._set_card_queryset_by_owner(owner)

        elif self.instance and getattr(self.instance, "pk", None):
            # GET（編集表示）
            self._set_card_queryset_by_owner(getattr(self.instance, "owner", None))

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
                if card.kind != "CARD":
                    self.add_error("card", "カードを選択してね。")
                if owner and card.owner != owner:
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

    def _set_account_queryset_by_owner(self, owner: str | None):
        """
        何をする？
        - owner があれば、そのownerの口座だけに絞る
        - owner がなければ、全口座にする（POSTバリデーション落ち回避）
        """
        if owner:
            self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT", owner=owner).order_by("owner", "id")
        else:
            self.fields["account"].queryset = Account.objects.filter(kind="ACCOUNT").order_by("owner", "id")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        today = timezone.localdate()
        self.fields["month"].initial = normalize_month(today)

        # ✅ 初期は空（JSが入れる想定）。ただし POST/編集では必ず復元する
        self.fields["account"].queryset = Account.objects.none()

        # --- queryset復元（重要）---
        if self.data:
            owner = (self.data.get("owner") or "").strip() or None
            if not owner and self.instance and getattr(self.instance, "pk", None):
                # 編集インスタンスから推定
                try:
                    owner = self.instance.account.owner
                except Exception:
                    owner = None
            self._set_account_queryset_by_owner(owner)

        elif self.instance and getattr(self.instance, "pk", None):
            # GET（編集表示）
            try:
                self.fields["owner"].initial = self.instance.account.owner
                self._set_account_queryset_by_owner(self.instance.account.owner)
            except Exception:
                self._set_account_queryset_by_owner(None)

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