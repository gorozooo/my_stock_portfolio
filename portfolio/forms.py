from django import forms
from django.core.exceptions import ValidationError
from django.conf import settings
from .models import Holding
from .services import tickers as svc_tickers
from .services import trend as svc_trend

def _normalize_code_head(raw: str) -> str:
    t = (raw or "").strip()
    if not t:
        return ""
    norm = svc_trend._normalize_ticker(t)  # '167A' -> '167A.T', '7011' -> '7011.T'
    head = norm.split(".", 1)[0] if norm else t.upper()
    if not (3 <= len(head) <= 5) or any(c not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for c in head.upper()):
        return ""
    return head.upper()

class HoldingForm(forms.ModelForm):
    opened_at = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = Holding
        fields = ["ticker","name","broker","account","side","quantity","avg_cost","opened_at","memo"]
        widgets = {
            "ticker":   forms.TextInput(attrs={"placeholder":"例: 7203 / 167A"}),
            "name":     forms.TextInput(attrs={"placeholder":"例: トヨタ自動車"}),
            "quantity": forms.NumberInput(attrs={"min":"1","step":"1"}),
            "avg_cost": forms.NumberInput(attrs({"min":"0","step":"0.01"})),
            "memo":     forms.Textarea(attrs={"rows":4,"style":"resize:vertical;","placeholder":"売買理由など"}),
        }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for k, f in self.fields.items():
            if k not in ("opened_at","memo"):
                f.required = True
                f.widget.attrs["required"] = "required"

    def clean_ticker(self):
        head = _normalize_code_head(self.cleaned_data.get("ticker"))
        if not head:
            raise ValidationError("コード形式が正しくありません（例: 7203 / 167A）。")
        return head

    def _resolve_name_fallback(self, code_head: str, raw: str) -> str:
        # ★ 0) 上書き辞書を最優先
        override = getattr(settings, "TSE_NAME_OVERRIDES", {}).get((code_head or "").upper())
        if override:
            return override

        name = None
        # 1) 4桁数字なら tickers.resolve_name（軽量）
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
        # 3) yfinance
        if not name:
            try:
                norm = svc_trend._normalize_ticker(code_head or raw)
                name = svc_trend._fetch_name_prefer_jp(norm)
            except Exception:
                pass
        return (name or "").strip()

    def clean(self):
        cd = super().clean()
        req = ["ticker","name","broker","account","side","quantity","avg_cost"]
        missing = [k for k in req if not str(cd.get(k) or "").strip()]
        if "name" in missing:
            missing.remove("name")

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