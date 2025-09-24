# portfolio/forms.py
from django import forms
from .models import Holding

class HoldingForm(forms.ModelForm):
    class Meta:
        model = Holding
        fields = [
            "ticker", "name", "broker", "side", "account",
            "quantity", "avg_cost", "opened_at"
        ]