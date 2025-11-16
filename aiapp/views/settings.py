# aiapp/views/settings.py
from __future__ import annotations

import os
from typing import Any, Dict

import yaml
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from portfolio.models import UserSetting
from . import settings as _  # noqa: F401, keep
from aiapp.services.broker_summary import compute_broker_summaries


# ============================
# タブ判定
# ============================
def _get_tab(request: HttpRequest) -> str:
    """
    ?tab=basic / summary / advanced を取得。
    想定外の値が来たときは basic にフォールバック。
    """
    t = (request.GET.get("tab") or request.POST.get("tab") or "basic").lower()
    return "basic" if t not in ("basic", "summary", "advanced") else t


# ============================
# ポリシーファイル関連
# ============================
def _policy_file_path() -> str:
    """
    short_aggressive.yml のフルパスを、ファイル構成から逆算して求める。

    views.py  … aiapp/views/settings.py
    aiapp_dir … aiapp/
    policy    … aiapp/policies/short_aggressive.yml
    """
    here = os.path.abspath(os.path.dirname(__file__))  # aiapp/views
    aiapp_dir = os.path.dirname(here)                  # aiapp
    return os.path.join(aiapp_dir, "policies", "short_aggressive.yml")


def _load_policy_raw() -> Dict[str, Any]:
    """
    short_aggressive.yml を**直接**読み込んで dict を返す。
    見つからない/壊れている場合は {}。
    """
    path = _policy_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}
    return data


def _build_policy_context() -> Dict[str, Any]:
    """
    ポリシーファイルの中身を UI 用に薄く整形して返す。
    （risk_pct / credit_usage_pct もここから直接読む）
    """
    data = _load_policy_raw()

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
    それ以外(filters/feesなど)はそのまま残す。
    """
    policy_path = _policy_file_path()
    data = _load_policy_raw()

    # UI からの値を反映
    data["risk_pct"] = float(risk_pct)
    data["credit_usage_pct"] = float(credit_usage_pct)

    os.makedirs(os.path.dirname(policy_path), exist_ok=True)
    with open(policy_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# ============================
# メインビュー
# ============================
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
    policy_risk = policy_ctx.get("risk_pct")
    policy_credit = policy_ctx.get("credit_usage_pct")

    risk_pct = float(policy_risk if policy_risk is not None else (us.risk_pct or 1.0))
    credit_usage_pct = float(policy_credit if policy_credit is not None else 70.0)

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

        # 信用余力の使用上限（％）もポリシーに反映
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

        # ポリシーファイルへ反映（ポリシーを真実ソースに保つ）
        _save_policy_basic_params(
            risk_pct=risk_pct,
            credit_usage_pct=credit_usage_pct,
        )

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

    # 再度ポリシーを読み直して、拡張タブに最新値を出す
    policy_ctx = _build_policy_context()

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