from django import forms
from .models import Holding

class HoldingForm(forms.ModelForm):
    opened_at = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"})
    )

    class Meta:
        model = Holding
        fields = [
            "ticker", "name", "broker", "account", "side",
            "quantity", "avg_cost", "opened_at",
        ]
        widgets = {
            "ticker":   forms.TextInput(attrs={"placeholder": "例: 7203"}),
            "name":     forms.TextInput(attrs={"placeholder": "例: トヨタ自動車"}),
            "quantity": forms.NumberInput(attrs={"min": "0", "step": "1"}),
            "avg_cost": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
        }