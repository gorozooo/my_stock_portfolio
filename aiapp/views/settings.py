# aiapp/views/settings.py
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect

from portfolio.models import UserSetting
from . import settings as _  # noqa: F401, keep
from aiapp.services.broker_summary import compute_broker_summaries
from aiapp.services.policy_loader import load_short_aggressive_policy


def _get_tab(request: HttpRequest) -> str:
    """
    ?tab=basic / summary / advanced を取得。
    想定外の値が来たときは basic にフォールバック。
    """
    t = (request.GET.get("tab") or request.POST.get("tab") or "basic").lower()
    return "basic" if t not in ("basic", "summary", "advanced") else t


def _build_policy_context() -> dict:
    """
    short_aggressive.yml の中身を UI 用に薄く整形して返す。
    """
    data = load_short_aggressive_policy() or {}

    filters = data.get("filters") or {}
    fees = data.get("fees") or {}

    return {
        "mode": data.get("mode") or "",
        "risk_pct": data.get("risk_pct"),
        "credit_usage_pct": data.get("credit_usage_pct"),
        "min_net_profit_yen": filters.get("min_net_profit_yen"),
        "min_reward_risk": filters.get("min_reward_risk"),
        "allow_negative_pl": filters.get("allow_negative_pl"),
        "commission_rate": fees.get("commission_rate"),
        "min_commission": fees.get("min_commission"),
        "slippage_rate": fees.get("slippage_rate"),
    }


@login_required
@transaction.atomic
def settings_view(request: HttpRequest) -> HttpResponse:
    user = request.user
    tab = _get_tab(request)

    # --- UserSetting を取得/作成 --------------------------------------------
    us, _created = UserSetting.objects.get_or_create(
        user=user,
        defaults={
            "account_equity": 1_000_000,
            "risk_pct": 1.0,
        },
    )

    # モデルに定義されているフィールド名に統一
    leverage_rakuten = us.leverage_rakuten
    haircut_rakuten = us.haircut_rakuten
    leverage_matsui = us.leverage_matsui
    haircut_matsui = us.haircut_matsui

    # ------------------------------------------------------------------ POST
    if request.method == "POST":
        tab = _get_tab(request)  # hidden で送っているタブ

        # リスク％
        def parse_float(name: str, current: float) -> float:
            v = request.POST.get(name)
            if v in (None, ""):
                return current
            try:
                return float(v)
            except ValueError:
                return current

        us.risk_pct = parse_float("risk_pct", us.risk_pct or 1.0)

        # 倍率 / ヘアカット（フィールド名に合わせる）
        leverage_rakuten = parse_float("leverage_rakuten", leverage_rakuten)
        haircut_rakuten = parse_float("haircut_rakuten", haircut_rakuten)
        leverage_matsui = parse_float("leverage_matsui", leverage_matsui)
        haircut_matsui = parse_float("haircut_matsui", haircut_matsui)

        us.leverage_rakuten = leverage_rakuten
        us.haircut_rakuten = haircut_rakuten
        us.leverage_matsui = leverage_matsui
        us.haircut_matsui = haircut_matsui

        us.save()
        messages.success(request, "保存しました")

        # 同じタブに戻る
        return redirect(f"{request.path}?tab={tab}")

    # ----------------------------------------------------------------- GET
    brokers = compute_broker_summaries(
        user=user,
        risk_pct=float(us.risk_pct or 1.0),
        # services 側は引数名どうでもいいので、そのまま渡す
        rakuten_leverage=leverage_rakuten,
        rakuten_haircut=haircut_rakuten,
        matsui_leverage=leverage_matsui,
        matsui_haircut=haircut_matsui,
    )

    ctx = {
        "tab": tab,
        "risk_pct": float(us.risk_pct or 1.0),

        # 表示用（モデルの名前に合わせる）
        "leverage_rakuten": leverage_rakuten,
        "haircut_rakuten": haircut_rakuten,
        "leverage_matsui": leverage_matsui,
        "haircut_matsui": haircut_matsui,

        "brokers": brokers,
        # 拡張タブ用：ポリシーの中身
        "policy": _build_policy_context(),
    }
    return render(request, "aiapp/settings.html", ctx)