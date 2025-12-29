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

    # 画面に出す broker 一覧（choicesから）
    broker_choices = getattr(Holding, "BROKER_CHOICES", (
        ("RAKUTEN", "楽天証券"),
        ("SBI", "SBI証券"),
        ("MATSUI", "松井証券"),
        ("OTHER", "その他"),
    ))

    if request.method == "POST":
        # 既存
        setting.account_equity = _to_int(request.POST.get("account_equity"), default=0)
        setting.risk_pct = _to_float(request.POST.get("risk_pct"), default=1.0)

        # 既存（UserSettingにあるので、画面でも編集できるようにする）
        setting.credit_usage_pct = _to_float(request.POST.get("credit_usage_pct"), default=setting.credit_usage_pct)

        setting.leverage_rakuten = _to_float(request.POST.get("leverage_rakuten"), default=setting.leverage_rakuten)
        setting.haircut_rakuten = _to_float(request.POST.get("haircut_rakuten"), default=setting.haircut_rakuten)

        setting.leverage_matsui = _to_float(request.POST.get("leverage_matsui"), default=setting.leverage_matsui)
        setting.haircut_matsui = _to_float(request.POST.get("haircut_matsui"), default=setting.haircut_matsui)

        setting.leverage_sbi = _to_float(request.POST.get("leverage_sbi"), default=setting.leverage_sbi)
        setting.haircut_sbi = _to_float(request.POST.get("haircut_sbi"), default=setting.haircut_sbi)

        # ★ 追加：年間目標（全体）
        setting.year_goal_total = _to_int(request.POST.get("year_goal_total"), default=setting.year_goal_total)

        # ★ 追加：年間目標（証券会社別）→ JSONに詰める
        by_broker = {}
        for key, _label in broker_choices:
            field_name = f"year_goal_broker_{key}"
            amt = _to_int(request.POST.get(field_name), default=0)
            if amt and amt != 0:
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
        },
    )