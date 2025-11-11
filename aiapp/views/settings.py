# -*- coding: utf-8 -*-
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django import forms
from django.shortcuts import render, redirect

# ★ 修正ポイント：users.models ではなく portfolio.models を参照
from portfolio.models import UserSetting


class AISettingsForm(forms.ModelForm):
    """
    AI 用の基本設定フォーム。
    ひとまずリスク％と口座残高を編集できるようにする（将来拡張可）。
    """
    class Meta:
        model = UserSetting
        fields = ["account_equity", "risk_pct"]
        labels = {
            "account_equity": "口座残高(円)",
            "risk_pct": "1トレードのリスク％",
        }
        help_texts = {
            "account_equity": "現物・信用の基準となる資金。将来は証券会社ごとの自動集計へ置換予定。",
            "risk_pct": "例: 1.0（=口座残高の1%を1回の損失上限とする）",
        }
        widgets = {
            "account_equity": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": 1000}),
            "risk_pct": forms.NumberInput(attrs={"class": "form-control", "min": 0, "max": 100, "step": 0.1}),
        }


@login_required
def settings_view(request):
    """
    /aiapp/settings/ : AI設定画面
    - 初回アクセス時に UserSetting を自動作成（get_or_create）
    - 保存後は同ページへリダイレクトして「保存しました」を表示
    """
    setting, _created = UserSetting.objects.get_or_create(
        user=request.user,
        defaults={
            "account_equity": 1_000_000,
            "risk_pct": 1.0,
        },
    )

    if request.method == "POST":
        form = AISettingsForm(request.POST, instance=setting)
        if form.is_valid():
            form.save()
            messages.success(request, "保存しました")
            return redirect("aiapp:settings")
    else:
        form = AISettingsForm(instance=setting)

    ctx = {
        "form": form,
        "page_title": "AI 設定",
    }
    return render(request, "aiapp/settings.html", ctx)