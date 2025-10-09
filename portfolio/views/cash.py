# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from ..models_cash import BrokerAccount
from ..services import cash_service as svc
from ..services import cash_updater as up


# ================== dashboard（存続） ==================
def _get_account(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    svc.ensure_default_accounts(currency=currency)
    return (
        BrokerAccount.objects.filter(broker=broker, currency=currency)
        .order_by("account_type")
        .first()
    )


@require_http_methods(["GET", "POST"])
def cash_dashboard(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        op = (request.POST.get("op") or "").strip()
        memo = (request.POST.get("memo") or "").strip()

        # 入金 / 出金
        if op in ("deposit", "withdraw"):
            broker = (request.POST.get("broker") or "").strip()
            if not broker:
                messages.error(request, "証券会社を選択してください。")
                return redirect("cash_dashboard")

            acc = _get_account(broker)
            if not acc:
                messages.error(request, f"{broker} の口座が見つかりません。")
                return redirect("cash_dashboard")

            try:
                amount_str = (request.POST.get("amount") or "").replace(",", "").strip()
                amount = int(amount_str)
                if amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")

                if op == "deposit":
                    svc.deposit(acc, amount, memo or "入金")
                    messages.success(request, f"{broker} に {amount:,} 円を入金しました。")
                else:
                    svc.withdraw(acc, amount, memo or "出金")
                    messages.success(request, f"{broker} から {amount:,} 円を出金しました。")
            except ValueError as e:
                messages.error(request, f"金額エラー：{e}")
            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")
            return redirect("cash_dashboard")

        # 口座間振替
        if op == "transfer":
            src_b = (request.POST.get("src_broker") or "").strip()
            dst_b = (request.POST.get("dst_broker") or "").strip()
            if not src_b or not dst_b:
                messages.error(request, "振替元/先の証券会社を選択してください。")
                return redirect("cash_dashboard")
            if src_b == dst_b:
                messages.error(request, "振替元と振替先が同じです。別の口座を選んでください。")
                return redirect("cash_dashboard")

            src = _get_account(src_b)
            dst = _get_account(dst_b)
            if not src or not dst:
                messages.error(request, "振替元/先の口座が見つかりません。")
                return redirect("cash_dashboard")

            try:
                amount_str = (request.POST.get("amount") or "").replace(",", "").strip()
                amount = int(amount_str)
                if amount <= 0:
                    raise ValueError("金額は正の整数で入力してください。")

                svc.transfer(src, dst, amount, memo or "口座間振替")
                messages.success(request, f"{src_b} → {dst_b} へ {amount:,} 円を振替えました。")
            except ValueError as e:
                messages.error(request, f"金額エラー：{e}")
            except Exception as e:
                messages.error(request, f"処理に失敗：{e}")
            return redirect("cash_dashboard")

        messages.error(request, "不正な操作が指定されました。")
        return redirect("cash_dashboard")

    # GET
    svc.ensure_default_accounts()
    today = date.today()

    # 自動同期（エラーでも画面は出す）
    try:
        info = up.sync_all()
        d = int(info.get("dividends_created", 0))
        r = int(info.get("realized_created", 0))
        if d or r or request.GET.get("force_toast") == "1":
            messages.info(request, f"同期完了\n・配当：新規 {d} 件\n・実損：新規 {r} 件")
    except Exception as e:
        messages.error(request, f"同期に失敗：{e}")

    brokers = svc.broker_summaries(today)
    kpi_total, _ = svc.total_summary(today)
    return render(request, "cash/dashboard.html", {"brokers": brokers, "kpi_total": kpi_total})