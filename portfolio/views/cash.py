# portfolio/views/cash.py
from __future__ import annotations
from django.shortcuts import render, redirect
from django.http import HttpRequest, HttpResponse
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from datetime import date

from ..models_cash import BrokerAccount
from ..services import cash_service as svc
from ..services import cash_updater as up


def _get_account(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    """
    口座区分は使わない方針。証券会社ごとの“代表口座（現物）”を取得。
    無ければ ensure_default_accounts() により自動作成される。
    ※ broker は「楽天 / 松井 / SBI / OTHER」など、既存レコードの broker 表記に合わせて渡すこと。
    """
    # 代表口座の自動作成（未作成でも落ちないように常に呼ぶ）
    svc.ensure_default_accounts(currency=currency)
    return (
        BrokerAccount.objects
        .filter(broker=broker, currency=currency)
        .order_by("account_type")
        .first()
    )


@require_http_methods(["GET", "POST"])
def cash_dashboard(request: HttpRequest) -> HttpResponse:
    # -------------------------------
    # POST（入金/出金/振替）
    # -------------------------------
    if request.method == "POST":
        op = request.POST.get("op")  # "deposit" | "withdraw" | "transfer"
        # 数値でない入力が来ても落ちないように防御
        try:
            amount = int(request.POST.get("amount") or 0)
        except Exception:
            amount = 0
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

    # -------------------------------
    # GET（表示）
    #   1) 代表口座の存在を保証
    #   2) 表示直前に「配当/実損」を Ledger に自動反映（重複なし）
    #   3) KPI 集計して表示
    # -------------------------------
    svc.ensure_default_accounts()

    # 表示直前に毎回同期（配当/実損を CashLedger に反映）
    # ?sync=1 を付けた時だけ件数をメッセージ表示（普段はサイレント）
    sync_info = {"dividends_created": 0, "realized_created": 0}
    try:
        sync_info = up.sync_all()
        if request.GET.get("sync") == "1":
            d = sync_info.get("dividends_created", 0)
            r = sync_info.get("realized_created", 0)
            messages.info(request, f"同期完了：配当 {d} 件 / 実損 {r} 件を反映しました。")
    except Exception as e:
        # 同期に失敗しても画面は壊さず、ログだけユーザに通知
        if request.GET.get("sync") == "1":
            messages.error(request, f"同期に失敗：{e}")

    today = date.today()
    brokers = svc.broker_summaries(today)
    kpi_total, _ = svc.total_summary(today)  # 将来の総合KPI用に維持

    context = {
        "brokers": brokers,            # 証券会社ごとのKPI（余力/預り金/拘束/今月差分）
        "kpi_total": kpi_total,        # 総合KPI（現時点では未表示）
        "sync_info": sync_info,        # 必要ならテンプレで表示可能
    }
    return render(request, "cash/dashboard.html", context)