# portfolio/views/cash.py
from __future__ import annotations
from django.shortcuts import render, redirect
from django.http import HttpRequest, HttpResponse
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from datetime import date

from ..models_cash import BrokerAccount, CashLedger
from ..services import cash_service as svc
from ..services import cash_updater as up


def _get_account(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    """
    口座区分は使わない方針。証券会社ごとの“代表口座（現物）”を取得。
    無ければ ensure_default_accounts() により自動作成される。
    """
    svc.ensure_default_accounts(currency=currency)
    # BrokerAccount.broker は「楽天 / 松井 / SBI」などのラベル想定
    return (
        BrokerAccount.objects
        .filter(broker=broker, currency=currency)
        .order_by("account_type")
        .first()
    )


@require_http_methods(["GET", "POST"])
def cash_dashboard(request: HttpRequest) -> HttpResponse:
    # ---------- POST（入出金/振替） ----------
    if request.method == "POST":
        op = request.POST.get("op")  # "deposit" | "withdraw" | "transfer"
        amount = int(request.POST.get("amount") or 0)
        memo = request.POST.get("memo", "")

        if op in ("deposit", "withdraw"):
            broker = request.POST.get("broker", "")
            acc = _get_account(broker)
            if not acc:
                messages.error(request, f"{broker} の口座が見つかりません。")
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

    # ---------- GET（表示） ----------
    svc.ensure_default_accounts()
    today = date.today()

    # ★ 自動同期（重複は内部でユニーク制約によりスキップ）
    sync_info = {"dividends_created": 0, "realized_created": 0}
    try:
        sync_info = up.sync_all()
        d_new = int(sync_info.get("dividends_created", 0))
        r_new = int(sync_info.get("realized_created", 0))

        # 既存（累計反映済み）件数も合わせて表示
        CL = CashLedger
        d_existing = CL.objects.filter(source_type=CL.SourceType.DIVIDEND).count()
        r_existing = CL.objects.filter(source_type=CL.SourceType.REALIZED).count()

        # 改行つきメッセージ（トーストは pre-line で改行表示）
        msg = (
            "同期完了\n"
            f"・配当：新規 {d_new} 件 / 既存 {d_existing} 件\n"
            f"・実損：新規 {r_new} 件 / 既存 {r_existing} 件"
        )
        if d_new or r_new:
            messages.success(request, msg)   # 新規が1件でもあれば成功色
        else:
            messages.info(request, msg)      # 0件でも必ず出す
    except Exception as e:
        messages.error(request, f"同期に失敗：{e}")

    brokers = svc.broker_summaries(today)
    kpi_total, _ = svc.total_summary(today)  # 将来用

    context = {
        "brokers": brokers,       # 証券会社ごとのKPI
        "kpi_total": kpi_total,   # 未使用だが保持
        "sync_info": sync_info,   # サマリで使うならどうぞ
    }
    return render(request, "cash/dashboard.html", context)