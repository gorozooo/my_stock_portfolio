# portfolio/views/settings.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from ..models import UserSetting

@login_required
def trade_setting(request):
    setting, _ = UserSetting.objects.get_or_create(user=request.user)
    if request.method == "POST":
        setting.account_equity = int(request.POST.get("account_equity", setting.account_equity or 0))
        setting.risk_pct = float(request.POST.get("risk_pct", setting.risk_pct or 1.0))
        setting.save()
        return redirect("trade_setting")
    return render(request, "portfolio/trade_setting.html", {"setting": setting})