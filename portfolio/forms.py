# portfolio/forms.py
from django import forms
from .models import Holding

class HoldingForm(forms.ModelForm):
    class Meta:
        model = Holding
        fields = ["ticker", "name", "broker", "account_type", "shares", "unit_price", "trade_at"]