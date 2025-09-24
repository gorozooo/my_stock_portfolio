from django import forms
from django.core.exceptions import ValidationError
from .models import Holding
from .services import tickers as svc_tickers
from .services import trend as svc_trend

# ───────────────────────────────────────────────
# 4桁数字だけでなく 167A のような英数字コードも許容する正規化
# trend._normalize_ticker を使って '.T' 付きにそろえた後、保存時はヘッドを返す
# ───────────────────────────────────────────────
def _normalize_code_head(raw: str) -> str:
    """
    入力（'7203', '7203.T', '167A' など）を trend 準拠で正規化し、
    保存用の「ヘッド」コード（'7203', '167A'）を返す。
    """
    t = (raw or "").strip()
    if not t:
        return ""
    norm = svc_trend._normalize_ticker(t)  # '167A' -> '167A.T', '7203' -> '7203.T', そのまま海外ティッカーも許容
    head = norm.split(".", 1)[0] if norm else t.upper()
    # 3〜5文字の英数字のみ許容（日本のコード想定）。必要なら範囲を広げる。
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
        # opened_at と memo 以外は必須
        for k, f in self.fields.items():
            if k not in ("opened_at", "memo"):
                f.required = True
                f.widget.attrs["required"] = "required"

    # ★ ここを trend 準拠に変更（167A などもOK）
    def clean_ticker(self):
        head = _normalize_code_head(self.cleaned_data.get("ticker"))
        if not head:
            raise ValidationError("コード形式が正しくありません（例: 7203 / 167A）。")
        return head

    # 名前の解決（CSV→trend map→yfinance の順でフォールバック）
    def _resolve_name_fallback(self, code_head: str, raw: str) -> str:
        name = None
        # 1) 4桁数字のときだけ、軽量な tickers.resolve_name を試す（tickers.py を改変しないため）
        try:
            if code_head and len(code_head) == 4 and code_head.isdigit():
                name = svc_tickers.resolve_name(code_head)
        except Exception:
            pass
        # 2) trend の JSON/CSV マップ
        if not name:
            try:
                norm = svc_trend._normalize_ticker(code_head)  # '167A' -> '167A.T'
                name = svc_trend._lookup_name_jp_from_list(norm)
            except Exception:
                pass
        # 3) yfinance（外部到達可能ならここで最終取得）
        if not name:
            try:
                norm = svc_trend._normalize_ticker(code_head or raw)
                name = svc_trend._fetch_name_prefer_jp(norm)
            except Exception:
                pass
        return (name or "").strip()

    def clean(self):
        cd = super().clean()

        # 必須チェック（opened_at・memo 以外）
        req = ["ticker", "name", "broker", "account", "side", "quantity", "avg_cost"]
        missing = [k for k in req if not str(cd.get(k) or "").strip()]
        # name は後で補完を試すので一旦除外
        if "name" in missing:
            missing.remove("name")

        # 数量・単価のバリデーション
        try:
            q = int(cd.get("quantity"))
            if q <= 0:
                self.add_error("quantity", "1以上を入力してください。")
        except Exception:
            self.add_error("quantity", "整数で入力してください。")
        try:
            c = float(cd.get("avg_cost"))
            if c < 0:
                self.add_error("avg_cost", "0以上を入力してください。")
        except Exception:
            self.add_error("avg_cost", "数値で入力してください。")

        # name 補完（最後の砦）
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