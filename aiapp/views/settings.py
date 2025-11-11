# -*- coding: utf-8 -*-
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from users.models import UserSetting

@login_required
def settings_view(request):
    """AIアプリ設定画面（リスク％のみ。将来拡張予定）"""
    setting, _ = UserSetting.objects.get_or_create(user=request.user)

    if request.method == "POST":
        try:
            new_risk = float(request.POST.get("risk_pct") or 1.0)
            setting.risk_pct = new_risk
            setting.save()
            messages.success(request, "保存しました。")
            return redirect("aiapp:settings")
        except Exception as e:
            messages.error(request, f"保存に失敗しました: {e}")

    ctx = {
        "setting": setting,
    }
    return render(request, "aiapp/settings.html", ctx)