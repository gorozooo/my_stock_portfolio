from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

from ..models import UserSetting, Holding


def _to_int(v, default=0) -> int:
    try:
        s = str(v).replace(",", "").strip()
        if s == "":
            return default
        return int(float(s))
    except Exception:
        return default


def _to_float(v, default=0.0) -> float:
    try:
        s = str(v).replace(",", "").strip()
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


@login_required
def trade_setting(request):
    setting, _ = UserSetting.objects.get_or_create(user=request.user)

    broker_choices = getattr(Holding, "BROKER_CHOICES", (
        ("RAKUTEN", "楽天証券"),
        ("SBI", "SBI証券"),
        ("MATSUI", "松井証券"),
        ("OTHER", "その他"),
    ))

    # テンプレで使いやすいように list 化
    by_broker = setting.year_goal_by_broker or {}
    broker_goals_for_template = []
    for key, label in broker_choices:
        broker_goals_for_template.append({
            "key": key,
            "label": label,
            "value": int(by_broker.get(key, 0) or 0),
        })

    if request.method == "POST":
        setting.account_equity = _to_int(request.POST.get("account_equity"), default=0)
        setting.risk_pct = _to_float(request.POST.get("risk_pct"), default=1.0)

        setting.year_goal_total = _to_int(request.POST.get("year_goal_total"), default=0)

        new_by_broker = {}
        for key, _label in broker_choices:
            amt = _to_int(request.POST.get(f"year_goal_broker_{key}"), default=0)
            if amt != 0:
                new_by_broker[key] = amt

        setting.year_goal_by_broker = new_by_broker
        setting.save()

        return redirect("trade_setting")

    return render(
        request,
        "portfolio/trade_setting.html",
        {
            "setting": setting,
            "broker_goals": broker_goals_for_template,
        },
    )