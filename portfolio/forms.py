from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.conf import settings
from django.utils import timezone

from .models import Holding, Dividend
from .services import tickers as svc_tickers
from .services import trend as svc_trend


def _normalize_code_head(raw: str) -> str:
    """
    入力（'7203', '7203.T', '167A' 等）を trend 準拠で正規化し、
    保存用のヘッドコード（'7203' / '167A'）を返す。
    """
    t = (raw or "").strip()
    if not t:
        return ""
    norm = svc_trend._normalize_ticker(t)  # 例: '167A' -> '167A.T'
    head = norm.split(".", 1)[0] if norm else t.upper()
    # 3〜5文字の英数字のみ許容（必要に応じて緩めてOK）
    if not (3 <= len(head) <= 5) or any(c not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in head.upper()):
        return ""
    return head.upper()


class HoldingForm(forms.ModelForm):
    opened_at = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = Holding
        fields = ["ticker", "name", "broker", "account", "side", "quantity", "avg_cost", "opened_at", "memo"]
        widgets = {
            "ticker":   forms.TextInput(attrs={"placeholder": "例: 7203 / 167A"}),
            "name":     forms.TextInput(attrs={"placeholder": "例: トヨタ自動車"}),
            "quantity": forms.NumberInput(attrs={"min": "1", "step": "1"}),
            "avg_cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "memo":     forms.Textarea(attrs={"rows": 4, "style": "resize:vertical;", "placeholder": "売買理由など"}),
        }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        # opened_at / memo 以外は必須
        for k, f in self.fields.items():
            if k not in ("opened_at", "memo"):
                f.required = True
                f.widget.attrs["required"] = "required"

    def clean_ticker(self):
        head = _normalize_code_head(self.cleaned_data.get("ticker"))
        if not head:
            raise ValidationError("コード形式が正しくありません（例: 7203 / 167A）。")
        return head

    def _resolve_name_fallback(self, code_head: str, raw: str) -> str:
        # 0) 任意の上書き辞書（和名固定したいとき）
        override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get((code_head or "").upper())
        if override:
            return override

        name = None
        # 1) 軽量: 4桁数字だけ tickers.csv 参照
        try:
            if code_head and len(code_head) == 4 and code_head.isdigit():
                name = svc_tickers.resolve_name(code_head)
        except Exception:
            pass
        # 2) trend の JSON/CSV マップ
        if not name:
            try:
                norm = svc_trend._normalize_ticker(code_head)
                name = svc_trend._lookup_name_jp_from_list(norm)
            except Exception:
                pass
        # 3) 最終: 外部取得
        if not name:
            try:
                norm = svc_trend._normalize_ticker(code_head or raw)
                name = svc_trend._fetch_name_prefer_jp(norm)
            except Exception:
                pass
        return (name or "").strip()

    def clean(self):
        cd = super().clean()

        # 必須（opened_at・memo 以外）
        req = ["ticker", "name", "broker", "account", "side", "quantity", "avg_cost"]
        missing = [k for k in req if not str(cd.get(k) or "").strip()]
        if "name" in missing:  # name は最後に補完を試す
            missing.remove("name")

        # 数量
        try:
            q = int(cd.get("quantity"))
            if q <= 0:
                self.add_error("quantity", "1以上を入力してください。")
        except Exception:
            self.add_error("quantity", "整数で入力してください。")

        # 平均取得単価
        try:
            c = float(cd.get("avg_cost"))
            if c < 0:
                self.add_error("avg_cost", "0以上を入力してください。")
        except Exception:
            self.add_error("avg_cost", "数値で入力してください。")

        # 銘柄名の最終補完
        if not (cd.get("name") or "").strip():
            code_head = cd.get("ticker") or ""
            nm = self._resolve_name_fallback(code_head, cd.get("ticker") or "")
            if nm:
                cd["name"] = nm
            else:
                self.add_error("name", "銘柄名が見つかりません。手入力してください。")

        if missing:
            raise ValidationError("必須項目が入力されていません。")
        if self.errors:
            raise ValidationError("入力に誤りがあります。")
        return cd


class DividendForm(forms.ModelForm):
    """
    - holding は任意（未選択でも登録可）
    - holding 未選択なら ticker を必須にし、コードから銘柄名を自動補完
    - UI は税引後のみ（is_net は Hidden で True 固定）
    - 税率はチェックボックス（ON=20.315% / OFF=0%）で tax を自動計算して保存
    """

    holding = forms.ModelChoiceField(
        queryset=Holding.objects.none(), required=False, label="保有（任意）",
        widget=forms.Select(attrs={"class": "sel"})
    )

    ticker = forms.CharField(
        label="ティッカー/コード", required=False,
        widget=forms.TextInput(attrs={
            "id": "id_ticker", "class": "in", "placeholder": "例: 7203 / 167A",
            "inputmode": "text", "pattern": "[0-9A-Za-z]{3,6}"
        })
    )
    name = forms.CharField(
        label="銘柄名（任意）", required=False,
        widget=forms.TextInput(attrs={"id": "id_name", "class": "in", "placeholder": "自動補完／手入力可"})
    )

    date = forms.DateField(label="受取日", widget=forms.DateInput(attrs={"type": "date", "class": "in"}))
    amount = forms.DecimalField(
        label="受取額（税引後）", min_value=Decimal("0"),
        widget=forms.NumberInput(attrs={"class": "in", "inputmode": "decimal", "step": "0.01"})
    )

    # セレクトの代わりにトグル（ON=20.315% / OFF=0%）
    apply_tax = forms.BooleanField(label="国内源泉 20.315% を適用", required=False, initial=True)

    # 保存用（自動計算・画面非表示）
    tax = forms.DecimalField(required=False, widget=forms.HiddenInput())

    memo = forms.CharField(
        label="メモ", required=False,
        widget=forms.TextInput(attrs={"class": "in", "placeholder": "任意メモ"})
    )

    class Meta:
        model = Dividend
        fields = ["holding", "ticker", "name", "date", "amount", "apply_tax", "tax", "is_net", "memo"]
        widgets = {
            "is_net": forms.HiddenInput(),  # 税引後固定
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Holding.objects.all()
        if user is not None:
            qs = qs.filter(user=user)
        self.fields["holding"].queryset = qs.order_by("ticker", "name")
        # 税引後固定
        self.fields["is_net"].initial = True
        self.fields["is_net"].required = False

    # --- 銘柄名の補完ロジック（HoldingForm と同等） ---
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

    def clean(self):
        cd = super().clean()

        holding = cd.get("holding")
        raw_ticker = (cd.get("ticker") or "").strip()
        ticker = _normalize_code_head(raw_ticker) if raw_ticker else ""

        # holding 未選択なら ticker 必須
        if not holding and not ticker:
            self.add_error("ticker", "保有が未選択の場合はティッカー/コードを入力してください。")
        cd["ticker"] = ticker or raw_ticker  # 正規化できたら置換

        # 銘柄名の自動補完
        name = (cd.get("name") or "").strip()
        if not name:
            if holding:
                name = holding.name or name
                if not ticker:
                    cd["ticker"] = holding.ticker
            if not name:
                name = self._resolve_name_fallback(cd.get("ticker") or "", raw_ticker)
            cd["name"] = name

        # 税額を自動計算（チェックボックスで 20.315% / 0%）
        amount = Decimal(cd.get("amount") or 0)
        rate_pct = Decimal("20.315") if cd.get("apply_tax") else Decimal("0")
        rate = rate_pct / Decimal("100")
        cd["tax"] = (amount * rate).quantize(Decimal("0.01")) if rate_pct > 0 else Decimal("0")
        cd["is_net"] = True

        return cd