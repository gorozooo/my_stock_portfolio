# forms.py
from django import forms
from .models import Holding
# TSEリストから銘柄名を引くユーティリティ
from .services.tickers import resolve_name as _resolve_name

def _normalize_code(value: str) -> str:
    """
    '7203' / '7203.T' を 4桁コード '7203' に正規化。
    それ以外は元の文字列を返す（バリデーションはモデル任せ）。
    """
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

    def clean_ticker(self):
        v = self.cleaned_data.get("ticker", "")
        return _normalize_code(v)

    def clean(self):
        cd = super().clean()
        # name 未入力ならコードから自動補完
        ticker = cd.get("ticker") or ""
        if not (cd.get("name") or "").strip():
            name = _resolve_name(ticker)
            if name:
                cd["name"] = name
        return cd