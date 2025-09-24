from django import forms
from django.core.exceptions import ValidationError
from .models import Holding
from .services.tickers import resolve_name as _resolve_name

def _normalize_code(value: str) -> str:
    """'7203' / '7203.T' → '7203' に正規化（それ以外はそのまま）"""
    if not value:
        return value
    t = value.strip().upper()
    if "." in t:
        t = t.split(".", 1)[0]
    return t

class HoldingForm(forms.ModelForm):
    opened_at = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"})
    )

    class Meta:
        model = Holding
        fields = [
            "ticker", "name", "broker", "account", "side",
            "quantity", "avg_cost", "opened_at", "memo",
        ]
        widgets = {
            "ticker":   forms.TextInput(attrs={"placeholder": "例: 7203"}),
            "name":     forms.TextInput(attrs={"placeholder": "例: トヨタ自動車"}),
            "quantity": forms.NumberInput(attrs={"min": "0", "step": "1"}),
            "avg_cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "memo":     forms.Textarea(attrs={
                "placeholder":"売買理由やメモを自由に", "rows":4, "style":"resize:vertical;"
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # opened_at と memo 以外は必須にする
        for name, field in self.fields.items():
            if name not in ("opened_at", "memo"):
                field.required = True
        # HTML側にも required 属性を出しておく（UX向上）
        for name, field in self.fields.items():
            if name not in ("opened_at", "memo"):
                field.widget.attrs["required"] = "required"

    def clean_ticker(self):
        v = self.cleaned_data.get("ticker", "")
        return _normalize_code(v)

    def clean(self):
        cd = super().clean()

        # すべての必須項目が入っているか（opened_at・memoは除外）
        required_keys = ["ticker", "name", "broker", "account", "side", "quantity", "avg_cost"]
        missing = [k for k in required_keys if not str(cd.get(k) or "").strip()]
        if missing:
            raise ValidationError("必須項目が入力されていません。")

        # 数値バリデーション（0以上など、必要なら >0 に変更可）
        try:
            q = int(cd.get("quantity"))
            if q <= 0:
                self.add_error("quantity", "1以上を入力してください。")
        except Exception:
            self.add_error("quantity", "整数で入力してください。")

        try:
            cost = float(cd.get("avg_cost"))
            if cost < 0:
                self.add_error("avg_cost", "0以上を入力してください。")
        except Exception:
            self.add_error("avg_cost", "数値で入力してください。")

        # name が空なら最後にコードから補完してみる（バックストップ）
        if not (cd.get("name") or "").strip():
            nm = _resolve_name(cd.get("ticker", ""))
            if nm:
                cd["name"] = nm
            else:
                self.add_error("name", "銘柄名を入力してください。")

        if self.errors:
            # 既に add_error 済みのエラーがあれば ValidationError を上げ直す
            raise ValidationError("入力に誤りがあります。")

        return cd