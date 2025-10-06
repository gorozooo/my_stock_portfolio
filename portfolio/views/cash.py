# portfolio/views/cash.py
from __future__ import annotations
from django.shortcuts import render, redirect
from django.http import HttpRequest, HttpResponse
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from datetime import date

from ..models_cash import BrokerAccount
from ..services import cash_service as svc

def _get_account(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    """
    口座区分を使わない前提に合わせて、同一証券会社の“代表口座”を1つ取得。
    複数ある場合は最初の1つを使う（必要になればUIで選ばせる想定）。
    """
    try:
        return BrokerAccount.objects.filter(broker=broker, currency=currency).order_by("account_type").first()
    except BrokerAccount.DoesNotExist:
        return None

@require_http_methods(["GET", "POST"])
def cash_dashboard(request: HttpRequest) -> HttpResponse:
    # POST（入出金/振替）
    if request.method == "POST":
        op = request.POST.get("op")  # "deposit" | "withdraw" | "transfer"
        amount = int(request.POST.get("amount") or 0)
        memo = request.POST.get("memo", "")

        if op in ("deposit", "withdraw"):
            broker = request.POST.get("broker", "")
            acc = _get_account(broker)
            if not acc:
                messages.error(request, f"{broker} の口座が見つかりません。管理画面で作成してください。")
                return redirect("cash_dashboard")
            try:
                if amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")
                if op == "deposit":
                    svc.deposit(acc, amount, memo or "入金")
                    messages.success(request, f"{broker} に {amount:,} 円を入金しました。")
                else:
                    svc.withdraw(acc, amount, memo or "出金")
                    messages.success(request, f"{broker} から {amount:,} 円を出金しました。")
            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")
            return redirect("cash_dashboard")

        if op == "transfer":
            src_b = request.POST.get("src_broker", "")
            dst_b = request.POST.get("dst_broker", "")
            src = _get_account(src_b)
            dst = _get_account(dst_b)
            if not src or not dst:
                messages.error(request, "振替元/先の口座が見つかりません。")
                return redirect("cash_dashboard")
            try:
                if amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")
                svc.transfer(src, dst, amount, memo or "口座間振替")
                messages.success(request, f"{src_b} → {dst_b} へ {amount:,} 円を振替えました。")
            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")
            return redirect("cash_dashboard")

    # GET（表示）
    today = date.today()
    # 証券会社ごとのサマリ
    brokers = svc.broker_summaries(today)
    # 参考：ホーム統合などで使うなら全体KPIも取り出せる
    kpi_total, _ = svc.total_summary(today)

    context = {
        "brokers": brokers,   # ← これを主役に
        "kpi_total": kpi_total,  # 画面では非表示だが将来用に残す
    }
    return render(request, "cash/dashboard.html", context)