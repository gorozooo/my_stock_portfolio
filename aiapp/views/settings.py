# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.db.models import Sum

# ← 修正ポイント：Cash は models_cash から、他は models から
from portfolio.models import UserSetting, Holding
from portfolio.models_cash import BrokerAccount, CashLedger


# ---- 内部ヘルパ ----
def _get_usersetting(user) -> UserSetting:
    obj, _ = UserSetting.objects.get_or_create(user=user)
    return obj

def _cash_balance_for_broker(user, broker_label_jp: str) -> int:
    """
    BrokerAccount(broker='楽天'/'松井') の円建て opening_balance + ledgers を集計
    """
    accts = BrokerAccount.objects.filter(broker=broker_label_jp, currency="JPY")
    if not accts.exists():
        return 0
    total = 0
    for a in accts:
        led_sum = a.ledgers.aggregate(s=Sum("amount"))["s"] or 0
        total += int(a.opening_balance or 0) + int(led_sum)
    return int(total)

def _holding_value(user, *, broker_code: str, account: str) -> float:
    """
    Holding から (last_price * quantity) を合計
    broker_code: 'RAKUTEN' / 'MATSUI'
    account: 'SPEC' / 'MARGIN' / 'NISA'
    """
    qs = Holding.objects.filter(user=user, broker=broker_code, account=account)
    total = 0.0
    for h in qs:
        price = float(h.last_price or 0.0)
        qty = int(h.quantity or 0)
        total += price * qty
    return float(round(total))

@dataclass
class BrokerSummary:
    label: str            # 表示ラベル（楽天 / 松井）
    cash_yen: int         # 現金残高
    stock_acq_value: int  # 現物（特定）評価額（担保の対象・NISA除外）
    credit_limit: int     # 信用枠（概算）
    credit_available: int # 信用余力（概算）
    note: Optional[str] = ""


def _summary_for_rakuten(user) -> BrokerSummary:
    us = _get_usersetting(user)
    # cash
    cash = _cash_balance_for_broker(user, "楽天")
    # SPEC/MARGIN の評価額（NISAは除外）
    spec_val   = _holding_value(user, broker_code="RAKUTEN", account="SPEC")
    margin_val = _holding_value(user, broker_code="RAKUTEN", account="MARGIN")

    L  = float(us.leverage_rakuten or 0.0)
    HC = float(us.haircut_rakuten  or 0.0)
    collateral   = spec_val * (1.0 - HC)
    credit_limit = (cash + collateral) * L
    used_margin  = margin_val / L if L > 0 else 0.0
    credit_avail = max(0.0, credit_limit - used_margin)

    return BrokerSummary(
        label="楽天",
        cash_yen=int(round(cash)),
        stock_acq_value=int(round(spec_val)),
        credit_limit=int(round(credit_limit)),
        credit_available=int(round(credit_avail)),
        note=f"倍率 {L:.2f} / ヘアカット {HC:.2f}",
    )


def _summary_for_matsui(user) -> BrokerSummary:
    us = _get_usersetting(user)
    cash = _cash_balance_for_broker(user, "松井")
    spec_val   = _holding_value(user, broker_code="MATSUI", account="SPEC")
    margin_val = _holding_value(user, broker_code="MATSUI", account="MARGIN")

    L  = float(us.leverage_matsui or 0.0)
    HC = float(us.haircut_matsui  or 0.0)  # 既定0.00（要件通り）
    collateral   = spec_val * (1.0 - HC)
    credit_limit = (cash + collateral) * L
    used_margin  = margin_val / L if L > 0 else 0.0
    credit_avail = max(0.0, credit_limit - used_margin)

    return BrokerSummary(
        label="松井",
        cash_yen=int(round(cash)),
        stock_acq_value=int(round(spec_val)),
        credit_limit=int(round(credit_limit)),
        credit_available=int(round(credit_avail)),
        note=f"倍率 {L:.2f} / ヘアカット {HC:.2f}",
    )


@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    us = _get_usersetting(request.user)

    # 保存（基本設定タブ）
    if request.method == "POST":
        # リスク％
        if "risk_pct" in request.POST:
            try:
                us.risk_pct = float(request.POST.get("risk_pct", us.risk_pct))
            except Exception:
                pass

        # 楽天 倍率/ヘアカット
        if "leverage_rakuten" in request.POST:
            try:
                us.leverage_rakuten = float(request.POST.get("leverage_rakuten", us.leverage_rakuten))
            except Exception:
                pass
        if "haircut_rakuten" in request.POST:
            try:
                us.haircut_rakuten = float(request.POST.get("haircut_rakuten", us.haircut_rakuten))
            except Exception:
                pass

        # 松井 倍率/ヘアカット
        if "leverage_matsui" in request.POST:
            try:
                us.leverage_matsui = float(request.POST.get("leverage_matsui", us.leverage_matsui))
            except Exception:
                pass
        if "haircut_matsui" in request.POST:
            try:
                us.haircut_matsui = float(request.POST.get("haircut_matsui", us.haircut_matsui))
            except Exception:
                pass

        us.save()
        return redirect(reverse("aiapp:settings") + "?saved=1")

    # 証券会社サマリ（自動計算）— 表示順は 楽天 → 松井 固定
    brokers = [
        _summary_for_rakuten(request.user),
        _summary_for_matsui(request.user),
    ]

    ctx = {
        "risk_pct": us.risk_pct,
        "leverage_rakuten": us.leverage_rakuten,
        "haircut_rakuten": us.haircut_rakuten,
        "leverage_matsui": us.leverage_matsui,
        "haircut_matsui": us.haircut_matsui,
        "saved": request.GET.get("saved") == "1",
        "brokers": brokers,
    }
    return render(request, "aiapp/settings.html", ctx)