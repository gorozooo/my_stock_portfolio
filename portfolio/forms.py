from django import forms
from django.core.exceptions import ValidationError
from .models import Holding
from .services import tickers as svc_tickers
from .services import trend as svc_trend

def _normalize_code4(s: str) -> str:
    t = (s or "").strip().upper()
    if not t:
        return ""
    if "." in t:
        t = t.split(".", 1)[0]
    return t if (len(t) == 4 and t.isdigit()) else ""

class HoldingForm(forms.ModelForm):
    opened_at = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = Holding
        fields = ["ticker","name","broker","account","side","quantity","avg_cost","opened_at","memo"]
        widgets = {
            "ticker":   forms.TextInput(attrs={"placeholder":"例: 7203"}),
            "name":     forms.TextInput(attrs={"placeholder":"例: トヨタ自動車"}),
            "quantity": forms.NumberInput(attrs={"min":"1","step":"1"}),
            "avg_cost": forms.NumberInput(attrs={"min":"0","step":"0.01"}),
            "memo":     forms.Textarea(attrs={"rows":4,"style":"resize:vertical;","placeholder":"売買理由など"}),
        }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for k, f in self.fields.items():
            if k not in ("opened_at","memo"):
                f.required = True
                f.widget.attrs["required"] = "required"

    def clean_ticker(self):
        return _normalize_code4(self.cleaned_data.get("ticker"))

    def _resolve_name_fallback(self, code4: str, raw: str) -> str:
        name = None
        # 1) tickers
        try:
            if code4:
                name = svc_tickers.resolve_name(code4)
        except Exception:
            pass
        # 2) trend map
        if not name:
            try:
                t = f"{code4}.T" if code4 else str(raw or "")
                name = svc_trend._lookup_name_jp_from_list(t)
            except Exception:
                pass
        # 3) yfinance
        if not name:
            try:
                t = f"{code4}.T" if code4 else str(raw or "")
                name = svc_trend._fetch_name_prefer_jp(t)
            except Exception:
                pass
        return (name or "").strip()

    def clean(self):
        cd = super().clean()

        # 必須チェック
        req = ["ticker","name","broker","account","side","quantity","avg_cost"]
        missing = [k for k in req if not str(cd.get(k) or "").strip()]
        # “name” は後で補完を試みるのでいったん外す
        if "name" in missing:
            missing.remove("name")

        # 数値チェック
        try:
            q = int(cd.get("quantity"))
            if q <= 0: self.add_error("quantity", "1以上を入力してください。")
        except Exception:
            self.add_error("quantity", "整数で入力してください。")
        try:
            c = float(cd.get("avg_cost"))
            if c < 0: self.add_error("avg_cost", "0以上を入力してください。")
        except Exception:
            self.add_error("avg_cost", "数値で入力してください。")

        # name 補完（最後の砦）
        if not (cd.get("name") or "").strip():
            code4 = cd.get("ticker") or ""
            nm = self._resolve_name_fallback(code4, cd.get("ticker") or "")
            if nm:
                cd["name"] = nm
            else:
                self.add_error("name", "銘柄名が見つかりません。手入力してください。")

        if missing:
            raise ValidationError("必須項目が入力されていません。")
        if self.errors:
            raise ValidationError("入力に誤りがあります。")
        return cd