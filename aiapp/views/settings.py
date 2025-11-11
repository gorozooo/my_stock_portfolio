# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Q, F
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect

# 設定（ユーザー別）
from aiapp.models import UserSetting  # users.models ではなく aiapp.models に統一

# 現金・保有は portfolio 側のモデルを使用
from portfolio.models import BrokerAccount, CashLedger, Holding


# -------- ユーティリティ（自動計算） ---------------------------------

@dataclass
class BrokerSnapshot:
    key: str                  # "RAKUTEN" / "MATSUI"
    label: str                # 表示ラベル（楽天 / 松井）
    cash_yen: int             # 現金（Ledgerから算出）
    stock_acq_value: int      # 現物(SPEC)の取得額合計（口座の原価ベース）
    note: str = ""            # 追加メモ（将来用）


BROKER_LABELS = {
    "RAKUTEN": "楽天",
    "MATSUI": "松井",
    "SBI": "SBI",
}

# BrokerAccount は repo により broker 値が「楽天/松井」等の日本語表記のため、
# 統一キーに寄せる変換を行う（片方しか無ければある分だけ表示）
CANON_KEYS = {
    "楽天": "RAKUTEN",
    "RAKUTEN": "RAKUTEN",
    "松井": "MATSUI",
    "MATSUI": "MATSUI",
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
    現物(SPEC) の取得額合計。
    broker は Holding.broker の値に合わせて判定。
    """
    # Holding 側の broker は "RAKUTEN", "MATSUI", "SBI", ... の英語キーで持っている想定
    qs = Holding.objects.filter(
        broker=broker_canon,
        account="SPEC",               # NISA/信用は含めない
        quantity__gt=0,
    )
    # 取得額 = avg_cost * quantity の総和（Decimal→int化）
    total = 0
    for h in qs.only("avg_cost", "quantity"):
        try:
            total += int(float(h.avg_cost or 0) * int(h.quantity or 0))
        except Exception:
            pass
    return total


def _snapshot_for(broker_canon: str) -> Optional[BrokerSnapshot]:
    """
    BrokerAccount は日本語表記（楽天/松井）、Holding は英語キー（RAKUTEN/MATSUI）という
    混在を吸収してスナップショットを生成。
    """
    # BrokerAccount 側は日本語 "楽天"/"松井" のことが多い
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
    # 並びは固定：楽天 → 松井（SBIは今回UIに出さない）
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
        # リスク％のみ保存（残高は自動計算のため入力を受け付けない）
        try:
            risk_str = (request.POST.get("risk_pct") or "").strip()
            us.risk_pct = float(risk_str) if risk_str != "" else us.risk_pct
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