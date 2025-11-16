# aiapp/views/settings.py
from __future__ import annotations

import os
from typing import Any, Dict

import yaml
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from aiapp.services.broker_summary import compute_broker_summaries
from aiapp.services.policy_loader import load_short_aggressive_policy
from portfolio.models import UserSetting
from . import settings as _  # noqa: F401, keep


def _get_tab(request: HttpRequest) -> str:
    """
    ?tab=basic / summary / advanced を取得。
    想定外の値が来たときは basic にフォールバック。
    """
    t = (request.GET.get("tab") or request.POST.get("tab") or "basic").lower()
    return "basic" if t not in ("basic", "summary", "advanced") else t


def _build_policy_context() -> Dict[str, Any]:
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


def _save_policy_basic_params(risk_pct: float, credit_usage_pct: float) -> None:
    """
    ポリシーファイル short_aggressive.yml の
    risk_pct / credit_usage_pct だけを上書き保存する。
    """
    policy_path = os.path.join(
        settings.BASE_DIR, "aiapp", "policies", "short_aggressive.yml"
    )

    try:
        with open(policy_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}

    if not isinstance(data, dict):
        data = {}

    # ここで UI からの値を反映
    data["risk_pct"] = float(risk_pct)
    data["credit_usage_pct"] = float(credit_usage_pct)

    # 既存の filters / fees などはそのまま残す
    with open(policy_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


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

    # ポリシー値を読み込み（なければ UserSetting / デフォルトで補完）
    policy_ctx = _build_policy_context()
    risk_pct = float(
        (policy_ctx.get("risk_pct") is not None and policy_ctx.get("risk_pct"))
        or (us.risk_pct or 1.0)
    )
    credit_usage_pct = float(
        (policy_ctx.get("credit_usage_pct") is not None
         and policy_ctx.get("credit_usage_pct"))
        or 70.0
    )

    # 倍率 / ヘアカットは UserSetting 側を使う
    leverage_rakuten = us.leverage_rakuten
    haircut_rakuten = us.haircut_rakuten
    leverage_matsui = us.leverage_matsui
    haircut_matsui = us.haircut_matsui

    # ------------------------------------------------------------------ POST
    if request.method == "POST":
        tab = _get_tab(request)  # hidden で送っているタブ

        def parse_float(name: str, current: float) -> float:
            v = request.POST.get(name)
            if v in (None, ""):
                return current
            try:
                return float(v)
            except ValueError:
                return current

        # 1トレードリスク％（UI → ポリシー＆UserSetting に反映）
        risk_pct = parse_float("risk_pct", risk_pct)
        us.risk_pct = risk_pct

        # ★信用余力の使用上限（％）もポリシーに反映
        credit_usage_pct = parse_float("credit_usage_pct", credit_usage_pct)

        # 倍率 / ヘアカット（UserSetting）
        leverage_rakuten = parse_float("leverage_rakuten", leverage_rakuten)
        haircut_rakuten = parse_float("haircut_rakuten", haircut_rakuten)
        leverage_matsui = parse_float("leverage_matsui", leverage_matsui)
        haircut_matsui = parse_float("haircut_matsui", haircut_matsui)

        us.leverage_rakuten = leverage_rakuten
        us.haircut_rakuten = haircut_rakuten
        us.leverage_matsui = leverage_matsui
        us.haircut_matsui = haircut_matsui
        us.save()

        # ポリシーファイルへ反映（後戻りではなくポリシーを真実ソースに保つ）
        _save_policy_basic_params(risk_pct=risk_pct, credit_usage_pct=credit_usage_pct)

        messages.success(request, "保存しました")
        return redirect(f"{request.path}?tab={tab}")

    # ----------------------------------------------------------------- GET
    brokers = compute_broker_summaries(
        user=user,
        # 証券サマリの概算でも、ポリシー由来のリスク％を使う
        risk_pct=risk_pct,
        rakuten_leverage=leverage_rakuten,
        rakuten_haircut=haircut_rakuten,
        matsui_leverage=leverage_matsui,
        matsui_haircut=haircut_matsui,
    )

    ctx = {
        "tab": tab,
        "risk_pct": risk_pct,
        "credit_usage_pct": credit_usage_pct,

        "leverage_rakuten": leverage_rakuten,
        "haircut_rakuten": haircut_rakuten,
        "leverage_matsui": leverage_matsui,
        "haircut_matsui": haircut_matsui,

        "brokers": brokers,
        # 拡張タブ用：ポリシーの中身
        "policy": policy_ctx,
    }
    return render(request, "aiapp/settings.html", ctx)