# =========================================================
# [FILE] forms.py
# [PATH] <project_root>/tradeai/forms.py
#
# このファイルは何？
# - tradeai の入力フォームをまとめるファイルです。
# - 今回はウォッチリスト登録フォームを定義します。
# =========================================================

from django import forms


class TradeaiWatchlistForm(forms.Form):
    ticker = forms.CharField(
        label="証券コード",
        max_length=16,
        required=True,
        widget=forms.TextInput(
            attrs={
                "class": "w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white placeholder-slate-500",
                "placeholder": "例: 7203",
                "autocomplete": "off",
            }
        ),
    )

    name = forms.CharField(
        label="銘柄名（任意）",
        max_length=128,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white placeholder-slate-500",
                "placeholder": "例: トヨタ自動車",
                "autocomplete": "off",
            }
        ),
    )

    long_enabled = forms.BooleanField(
        label="ロング監視",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(
            attrs={
                "class": "h-5 w-5 rounded border-slate-600 bg-slate-900 text-cyan-400",
            }
        ),
    )

    short_enabled = forms.BooleanField(
        label="ショート監視",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(
            attrs={
                "class": "h-5 w-5 rounded border-slate-600 bg-slate-900 text-cyan-400",
            }
        ),
    )

    notify_enabled = forms.BooleanField(
        label="通知ON",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(
            attrs={
                "class": "h-5 w-5 rounded border-slate-600 bg-slate-900 text-cyan-400",
            }
        ),
    )

    priority = forms.IntegerField(
        label="優先度",
        min_value=1,
        max_value=999,
        required=True,
        initial=20,
        widget=forms.NumberInput(
            attrs={
                "class": "w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white placeholder-slate-500",
                "placeholder": "20",
            }
        ),
    )

    memo = forms.CharField(
        label="メモ（任意）",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "w-full rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-white placeholder-slate-500",
                "placeholder": "この銘柄を見たい理由など",
                "rows": 3,
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        long_enabled = cleaned_data.get("long_enabled")
        short_enabled = cleaned_data.get("short_enabled")

        if not long_enabled and not short_enabled:
            raise forms.ValidationError("ロング監視かショート監視のどちらかはONにしてください。")

        return cleaned_data