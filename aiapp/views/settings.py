# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect

# 正しいインポート先（分割モデル）
from portfolio.models import Holding, UserSetting
from portfolio.models_cash import BrokerAccount, CashLedger


# -------- ユーティリティ（自動計算） ---------------------------------

@dataclass
class BrokerSnapshot:
    key: str                  # "RAKUTEN" / "MATSUI"
    label: str                # 表示ラベル（楽天 / 松井）
    cash_yen: int             # 現金（Ledgerから算出）
    stock_acq_value: int      # 現物(SPEC)の取得額合計（原価ベース）
    note: str = ""            # 追加メモ（将来用）


BROKER_LABELS = {
    "RAKUTEN": "楽天",
    "MATSUI": "松井",
    "SBI": "SBI",
}

def _account_balance(account: BrokerAccount) -> int:
    """
    CashLedger 運用が最新一致という前提で、opening_balance + すべての増減を合算。
    """
    base = int(account.opening_balance or 0)
    delta = (
        CashLedger.objects.filter(account=account)
        .aggregate(s=Sum("amount"))
        .get("s") or 0
    )
    return int(base + int(delta))


def _spec_acquisition_value(broker_canon: str) -> int:
    """
    現物(SPEC) の取得額合計（avg_cost * quantity の総和）。
    Holding.broker は "RAKUTEN"/"MATSUI"/"SBI" の英語キー想定。
    """
    qs = Holding.objects.filter(
        broker=broker_canon,
        account="SPEC",
        quantity__gt=0,
    ).only("avg_cost", "quantity")
    total = 0
    for h in qs:
        try:
            total += int(float(h.avg_cost or 0) * int(h.quantity or 0))
        except Exception:
            pass
    return total


def _snapshot_for(broker_canon: str) -> Optional[BrokerSnapshot]:
    """
    BrokerAccount は日本語表記（楽天/松井）のことが多いので双方拾う。
    """
    ja = "楽天" if broker_canon == "RAKUTEN" else ("松井" if broker_canon == "MATSUI" else broker_canon)
    accs = BrokerAccount.objects.filter(broker__in=[ja, broker_canon], currency="JPY")
    if not accs.exists():
        return None

    cash_total = 0
    for acc in accs:
        cash_total += _account_balance(acc)

    stock_total = _spec_acquisition_value(broker_canon)

    return BrokerSnapshot(
        key=broker_canon,
        label=BROKER_LABELS.get(broker_canon, broker_canon),
        cash_yen=int(cash_total),
        stock_acq_value=int(stock_total),
    )


def _collect_brokers() -> List[BrokerSnapshot]:
    out: List[BrokerSnapshot] = []
    # 並び固定：楽天 → 松井（SBIは今回UI非表示）
    for key in ["RAKUTEN", "MATSUI"]:
        snap = _snapshot_for(key)
        if snap:
            out.append(snap)
    return out


# -------- View -----------------------------------------------------------

@login_required
def settings_view(request: HttpRequest) -> HttpResponse:
    # ユーザー設定（無ければ作成）
    us, _ = UserSetting.objects.get_or_create(user=request.user)

    if request.method == "POST":
        # リスク％のみ保存（残高は自動計算）
        try:
            risk_str = (request.POST.get("risk_pct") or "").strip()
            if risk_str != "":
                us.risk_pct = float(risk_str)
            us.save()
            messages.success(request, "保存しました")
            return redirect("aiapp:settings")
        except Exception:
            messages.error(request, "保存に失敗しました")

    # 証券会社スナップショット（自動計算）
    brokers = _collect_brokers()

    ctx = {
        "risk_pct": us.risk_pct,
        "brokers": brokers,  # [{label, cash_yen, stock_acq_value}, ...]
    }
    return render(request, "aiapp/settings.html", ctx)