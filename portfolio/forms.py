from django import forms
from .models import SettingsPassword

class SettingsPasswordForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, label="設定画面パスワード")

    class Meta:
        model = SettingsPassword
        fields = ['password']
