from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.conf import settings

from .models import Holding, Dividend
from .services import tickers as svc_tickers
from .services import trend as svc_trend


def _normalize_code_head(raw: str) -> str:
    """
    入力（'7203', '7203.T', '167A', 'AAPL' 等）を trend 準拠で正規化し、
    保存用ヘッドコード（'7203' / '167A' / 'AAPL'）を返す。
    """
    t = (raw or "").strip()
    if not t:
        return ""
    norm = svc_trend._normalize_ticker(t)
    head = norm.split(".", 1)[0] if norm else t.upper()
    if not (1 <= len(head) <= 5):
        return ""
    if any(c not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in head.upper()):
        return ""
    return head.upper()


# ======================================================================
# ★ 追加：日本株/米国株 判定
# ======================================================================
import re

def _detect_market_currency(head: str) -> tuple[str, str]:
    """
    入力コード head に応じて market/currency を判定する。
    - 4〜5桁の数字 → 日本株（JP/JPY）
    - 3桁数字 + 英字 → 日本株（JP/JPY）
    - 英字のみ or 英字を含む → 米国株（US/USD）
    """
    if re.fullmatch(r"[0-9]{4,5}", head):
        return "JP", "JPY"
    if re.fullmatch(r"[0-9]{3}[A-Z]", head):
        return "JP", "JPY"
    # AAPL / MSFT / TSLA など
    if re.search(r"[A-Z]", head):
        return "US", "USD"
    return "JP", "JPY"



# ======================================================================
# HoldingForm
# ======================================================================
class HoldingForm(forms.ModelForm):
    opened_at = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = Holding
        fields = [
            "ticker",
            "name",
            "sector",
            "broker",
            "account",
            "side",
            "quantity",
            "avg_cost",
            "opened_at",
            "memo",
            "market",     # ★ 自動判定（Hidden）
            "currency",   # ★ 自動判定（Hidden）
            "fx_rate",    # ★ 追加：取得時の為替レート（手入力）
        ]
        widgets = {
            "ticker": forms.TextInput(attrs={
                "placeholder": "例: 7203 / 167A / AAPL",
                "inputmode": "text",
                "pattern": "[A-Za-z0-9]*",
                "autocapitalize": "characters",
                "autocorrect": "off",
                "autocomplete": "off",
                "spellcheck": "false",
                "style": "ime-mode: disabled;",
                "oninput": "this.value = this.value.replace(/[^0-9A-Za-z]/g,'').toUpperCase();",
            }),
            "name":     forms.TextInput(attrs={"placeholder": "例: トヨタ"}),
            "sector":   forms.TextInput(attrs={"placeholder": "例: 輸送用機器"}),
            "quantity": forms.NumberInput(attrs={"min": "1", "step": "1"}),
            "avg_cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "memo":     forms.Textarea(attrs={"rows": 4, "style": "resize:vertical;", "placeholder": "売買理由など"}),
            # ★ market / currency はフォームUIから隠す（コードから自動判定）
            "market":   forms.HiddenInput(),
            "currency": forms.HiddenInput(),
            # ★ fx_rate はユーザーが入力する（例: 155.250000）
            "fx_rate":  forms.NumberInput(attrs={
                "min": "0",
                "step": "0.000001",
                "placeholder": "例: 155.250000（1USDあたりの円レート）",
            }),
        }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        # 基本必須（opened_at / memo / sector / market / currency / fx_rate は任意）
        for k, f in self.fields.items():
            if k not in ("opened_at", "memo", "sector", "market", "currency", "fx_rate"):
                f.required = True
                f.widget.attrs["required"] = "required"

    def clean_ticker(self):
        head = _normalize_code_head(self.cleaned_data.get("ticker"))
        if not head:
            raise ValidationError("コード形式が正しくありません（例: 7203 / 167A / AAPL）。")
        return head

    def _resolve_name_fallback(self, code_head: str, raw: str) -> str:
        override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get((code_head or "").upper())
        if override:
            return override

        name = None
        # 日本株（4桁数字）の場合は従来ロジックを優先
        try:
            if code_head and len(code_head) == 4 and code_head.isdigit():
                name = svc_tickers.resolve_name(code_head)
        except Exception:
            pass
        if not name:
            try:
                norm = svc_trend._normalize_ticker(code_head)
                name = svc_trend._lookup_name_jp_from_list(norm)
            except Exception:
                pass
        if not name:
            try:
                norm = svc_trend._normalize_ticker(code_head or raw)
                name = svc_trend._fetch_name_prefer_jp(norm)
            except Exception:
                pass
        return (name or "").strip()

    def clean_sector(self):
        return (self.cleaned_data.get("sector") or "").strip()

    def clean(self):
        cd = super().clean()

        # ---- 必須チェック ----
        req = ["ticker", "name", "broker", "account", "side", "quantity", "avg_cost"]
        missing = [k for k in req if not str(cd.get(k) or "").strip()]
        if "name" in missing:
            # 銘柄名はあとで補完を試みるので一旦外す
            missing.remove("name")

        # ---- 数量 ----
        try:
            q = int(cd.get("quantity"))
            if q <= 0:
                self.add_error("quantity", "1以上を入力してください。")
        except Exception:
            self.add_error("quantity", "整数で入力してください。")

        # ---- 平均取得単価 ----
        try:
            c = float(cd.get("avg_cost"))
            if c < 0:
                self.add_error("avg_cost", "0以上を入力してください。")
        except Exception:
            self.add_error("avg_cost", "数値で入力してください。")

        # ---- 銘柄名補完 ----
        if not (cd.get("name") or "").strip():
            code_head = cd.get("ticker") or ""
            nm = self._resolve_name_fallback(code_head, cd.get("ticker") or "")
            if nm:
                cd["name"] = nm
            else:
                self.add_error("name", "銘柄名が見つかりません。手入力してください。")

        # ==================================================================
        # ★ 追加：market/currency 自動判定
        # ==================================================================
        head = cd.get("ticker") or ""
        market, currency = _detect_market_currency(head)
        cd["market"] = market
        cd["currency"] = currency

        # ==================================================================
        # ★ 追加：fx_rate のバリデーション
        #   - 入力があれば > 0 の数値チェック
        #   - currency != JPY のときは必須（証券会社の約定レートを入れる）
        # ==================================================================
        fx = cd.get("fx_rate")
        cur = cd.get("currency")

        # 入力されている場合の数値チェック
        if fx not in (None, ""):
            try:
                fx_val = float(fx)
                if fx_val <= 0:
                    self.add_error("fx_rate", "0より大きい数値を入力してください。")
            except Exception:
                self.add_error("fx_rate", "数値で入力してください。")

        # 通貨が JPY 以外なら FX レート必須
        if cur and cur != "JPY" and not fx:
            self.add_error("fx_rate", "JPY以外の通貨のときは、約定時の為替レートを入力してください。")

        # ==================================================================
        # 最終チェック
        # ==================================================================
        if missing:
            raise ValidationError("必須項目が入力されていません。")
        if self.errors:
            raise ValidationError("入力に誤りがあります。")

        return cd


class DividendForm(forms.ModelForm):
    """
    - holding は任意（未選択でも登録可）
    - 受取額は「税引前」で入力 → 税率(0 / 20.315)から tax を算出して保存（is_net=False）
    - 保有を選ぶと broker/account/purchase_price/quantity を初期値補完（上書き可）
    - 保有未選択の場合：ticker 必須、さらに quantity / purchase_price も実質必須
    """

    # 保有（任意）
    holding = forms.ModelChoiceField(
        queryset=Holding.objects.none(), required=False, label="保有（任意）",
        widget=forms.Select(attrs={"class": "sel"})
    )

    # 保有がない銘柄も登録できるよう自由入力
    ticker = forms.CharField(
        label="ティッカー/コード（保有未選択時は必須）", required=False,
        widget=forms.TextInput(attrs={
            "id": "id_ticker", "class": "in", "placeholder": "例: 7203 / 167A",
            "inputmode": "text"
        })
    )
    name = forms.CharField(
        label="銘柄名（任意・自動補完可）", required=False,
        widget=forms.TextInput(attrs={"id": "id_name", "class": "in", "placeholder": "自動補完／手入力可"})
    )

    # 日付・金額（税引前）
    date = forms.DateField(label="受取日", widget=forms.DateInput(attrs={"type": "date", "class": "in"}))
    amount = forms.DecimalField(
        label="受取額（税引前）", min_value=Decimal("0"),
        widget=forms.NumberInput(attrs={"class": "in", "inputmode": "decimal", "step": "0.01", "id": "id_amount"})
    )

    # KPI 用（保有未選択でも利回りが出せるように）
    quantity = forms.IntegerField(
        label="株数", min_value=1, required=False,
        widget=forms.NumberInput(attrs={"class": "in", "inputmode": "numeric", "id": "id_quantity"})
    )
    purchase_price = forms.DecimalField(
        label="取得単価（1株）", min_value=Decimal("0"), required=False,
        widget=forms.NumberInput(attrs={"class": "in", "inputmode": "decimal", "step": "0.01", "id": "id_purchase_price"})
    )

    # 税率：テンプレ側でピルUI（checkbox）→ hidden に同期する前提
    tax_rate_pct = forms.CharField(
        label="税率", required=False, initial="20.315",
        widget=forms.HiddenInput(attrs={"id": "id_tax_rate_pct"})
    )

    # 区分
    broker = forms.ChoiceField(
        label="証券会社", choices=Dividend.BROKER_CHOICES, required=False,
        widget=forms.Select(attrs={"class": "sel", "id": "id_broker"})
    )
    account = forms.ChoiceField(
        label="口座区分", choices=Dividend.ACCOUNT_CHOICES, required=False,
        widget=forms.Select(attrs={"class": "sel", "id": "id_account"})
    )

    # 保存用（自動計算）
    tax = forms.DecimalField(required=False, widget=forms.HiddenInput())
    is_net = forms.BooleanField(required=False, widget=forms.HiddenInput())

    memo = forms.CharField(label="メモ", required=False,
                           widget=forms.TextInput(attrs={"class": "in", "placeholder": "任意メモ"}))

    class Meta:
        model = Dividend
        fields = [
            "holding", "ticker", "name",
            "date", "amount",
            "quantity", "purchase_price",
            "tax_rate_pct", "tax", "is_net",
            "broker", "account",
            "memo",
        ]

    # ---- 共通：銘柄名の補完（HoldingForm 同等方針） ----
    @staticmethod
    def _resolve_name_fallback(code_head: str, raw: str) -> str:
        override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get((code_head or "").upper())
        if override:
            return override
        name = None
        try:
            if code_head and len(code_head) == 4 and code_head.isdigit():
                name = svc_tickers.resolve_name(code_head)
        except Exception:
            pass
        if not name:
            try:
                norm = svc_trend._normalize_ticker(code_head or raw)
                name = svc_trend._lookup_name_jp_from_list(norm)
            except Exception:
                pass
        if not name:
            try:
                norm = svc_trend._normalize_ticker(code_head or raw)
                name = svc_trend._fetch_name_prefer_jp(norm)
            except Exception:
                pass
        return (name or "").strip()

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        # 自分の保有のみ
        qs = Holding.objects.all()
        if user is not None:
            qs = qs.filter(user=user)
        self.fields["holding"].queryset = qs.order_by("ticker", "name")

        # 既定値
        self.fields["is_net"].initial = False   # 税引前入力として扱う
        if not self.initial.get("broker"):
            self.initial["broker"] = "OTHER"
        if not self.initial.get("account"):
            self.initial["account"] = "SPEC"
        if not self.initial.get("tax_rate_pct"):
            self.initial["tax_rate_pct"] = "20.315"

        # holding 初期指定があれば各値を補完（上書き可能）
        inst_holding = None
        try:
            h_initial = self.initial.get("holding") or (self.instance and self.instance.holding_id)
            if h_initial:
                inst_holding = qs.filter(pk=h_initial).first()
        except Exception:
            pass
        if inst_holding:
            self.fields["broker"].initial = self.fields["broker"].initial or inst_holding.broker
            self.fields["account"].initial = self.fields["account"].initial or inst_holding.account
            if not self.initial.get("purchase_price"):
                self.initial["purchase_price"] = inst_holding.avg_cost
            if not self.initial.get("quantity"):
                self.initial["quantity"] = inst_holding.quantity

    def clean(self):
        cd = super().clean()

        holding = cd.get("holding")
        raw_ticker = (cd.get("ticker") or "").strip()
        head = _normalize_code_head(raw_ticker) if raw_ticker else ""

        # 保有未選択なら ticker 必須
        if not holding and not head:
            self.add_error("ticker", "保有が未選択の場合はティッカー/コードを入力してください。")
        cd["ticker"] = head or raw_ticker

        # 銘柄名補完（holding優先 → コードから補完）
        name = (cd.get("name") or "").strip()
        if not name:
            if holding:
                name = holding.name or name
                if not head:
                    cd["ticker"] = holding.ticker
            if not name:
                name = self._resolve_name_fallback(cd.get("ticker") or "", raw_ticker)
            cd["name"] = name

        # KPI用：holding 未選択時は quantity / purchase_price を実質必須
        qty = cd.get("quantity")
        if not holding and not qty:
            self.add_error("quantity", "株数を入力してください。")
        pp = cd.get("purchase_price")
        if not holding and (pp is None or pp == ""):
            self.add_error("purchase_price", "取得単価を入力してください。")

        # 税額を自動計算（amount は税引前 / is_net=False）
        gross = Decimal(cd.get("amount") or 0)
        # hidden から渡ってくる値（"0" または "20.315"）を解釈
        try:
            rate_pct = Decimal(str(cd.get("tax_rate_pct") or "20.315"))
        except Exception:
            rate_pct = Decimal("20.315")
        # 想定外の値は 20.315% にフォールバック
        if rate_pct not in (Decimal("0"), Decimal("20.315")):
            rate_pct = Decimal("20.315")
        rate = rate_pct / Decimal("100")
        cd["tax"] = (gross * rate).quantize(Decimal("0.01")) if rate_pct > 0 else Decimal("0")
        cd["is_net"] = False
        cd["tax_rate_pct"] = rate_pct  # 後工程で必要なら参照

        # broker/account 補完（holding があれば初期値として使う）
        if holding:
            cd["broker"] = cd.get("broker") or holding.broker or "OTHER"
            cd["account"] = cd.get("account") or holding.account or "SPEC"

        if self.errors:
            raise ValidationError("入力に誤りがあります。")
        return cd