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

    # テンプレでそのまま value に使えるように dict を用意
    broker_goal_map = setting.year_goal_by_broker or {}

    if request.method == "POST":
        # 既存
        setting.account_equity = _to_int(request.POST.get("account_equity"), default=0)
        setting.risk_pct = _to_float(request.POST.get("risk_pct"), default=1.0)

        # ★追加：年間目標（全体）
        setting.year_goal_total = _to_int(request.POST.get("year_goal_total"), default=0)

        # ★追加：年間目標（証券会社別）→ JSONへ
        by_broker = {}
        for key, _label in broker_choices:
            amt = _to_int(request.POST.get(f"year_goal_broker_{key}"), default=0)
            if amt != 0:
                by_broker[key] = amt

        setting.year_goal_by_broker = by_broker
        setting.save()

        return redirect("trade_setting")

    return render(
        request,
        "portfolio/trade_setting.html",
        {
            "setting": setting,
            "broker_choices": broker_choices,
            "broker_goal_map": broker_goal_map,
        },
    )