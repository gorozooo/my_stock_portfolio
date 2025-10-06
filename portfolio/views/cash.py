# portfolio/views/cash.py
from __future__ import annotations
from django.shortcuts import render, redirect
from django.http import HttpRequest, HttpResponse
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from datetime import date

from ..models_cash import BrokerAccount
from ..services import cash_service as svc

def _get_account(broker: str, account_type: str, currency: str = "JPY") -> BrokerAccount | None:
    try:
        return BrokerAccount.objects.get(broker=broker, account_type=account_type, currency=currency)
    except BrokerAccount.DoesNotExist:
        return None

@require_http_methods(["GET", "POST"])
def cash_dashboard(request: HttpRequest) -> HttpResponse:
    """
    💵 現金ダッシュボード（MVP）
    - GET: KPIと口座カード一覧を表示
    - POST: 入金/出金/振替を処理（簡易フォーム）
    """
    # POST（入出金/振替）
    if request.method == "POST":
        op = request.POST.get("op")  # "deposit" | "withdraw" | "transfer"
        broker = request.POST.get("broker", "")
        account_type = request.POST.get("account_type", "")
        amount = int(request.POST.get("amount") or 0)
        memo = request.POST.get("memo", "")

        if op in ("deposit", "withdraw"):
            acc = _get_account(broker, account_type)
            if not acc:
                messages.error(request, "口座が見つかりません。管理画面で作成してください。")
                return redirect("cash_dashboard")
            try:
                if amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")
                if op == "deposit":
                    svc.deposit(acc, amount, memo or "入金")
                    messages.success(request, f"{acc} に {amount:,} 円を入金しました。")
                else:
                    svc.withdraw(acc, amount, memo or "出金")
                    messages.success(request, f"{acc} から {amount:,} 円を出金しました。")
            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")
            return redirect("cash_dashboard")

        if op == "transfer":
            src_b = request.POST.get("src_broker", "")
            src_a = request.POST.get("src_account_type", "")
            dst_b = request.POST.get("dst_broker", "")
            dst_a = request.POST.get("dst_account_type", "")
            src = _get_account(src_b, src_a)
            dst = _get_account(dst_b, dst_a)
            if not src or not dst:
                messages.error(request, "振替元/先の口座が見つかりません。")
                return redirect("cash_dashboard")
            try:
                if amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")
                svc.transfer(src, dst, amount, memo or "口座間振替")
                messages.success(request, f"{src} → {dst} へ {amount:,} 円を振替えました。")
            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")
            return redirect("cash_dashboard")

    # GET（表示）
    today = date.today()
    kpi, rows = svc.total_summary(today)
    # 口座選択用（フォームのセレクト）
    accounts = BrokerAccount.objects.all().order_by("broker", "account_type")

    context = {
        "kpi": kpi,
        "rows": rows,
        "accounts": accounts,
        "action": request.GET.get("action", ""),
    }
    return render(request, "cash/dashboard.html", context)