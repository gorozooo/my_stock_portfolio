from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from ..models import UserSetting

@login_required
def trade_setting(request):
    setting, _ = UserSetting.objects.get_or_create(user=request.user)
    if request.method == "POST":
        setting.account_equity = request.POST.get("account_equity") or 0
        setting.risk_pct = request.POST.get("risk_pct") or 1.0
        setting.save()
        return redirect("trade_setting")

    return render(request, "portfolio/trade_setting.html", {"setting": setting})