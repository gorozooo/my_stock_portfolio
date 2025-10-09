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

        # 互換：口座間振替（UI非表示）
        if op == "transfer":
            src_b = (request.POST.get("src_broker") or "").strip()
            dst_b = (request.POST.get("dst_broker") or "").strip()
            if not src_b or not dst_b:
                messages.error(request, "振替元/先の証券会社を選択してください。")
                return redirect("cash_dashboard")
            if src_b == dst_b:
                messages.error(request, "振替元と振替先が同じです。")
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
            except Exception as e:
                messages.error(request, f"振替に失敗：{e}")
            return redirect("cash_dashboard")

        messages.error(request, "不正な操作です。")
        return redirect("cash_dashboard")

    # === GET ===
    svc.ensure_default_accounts()
    today = date.today()

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

    # ===== ⚠️ 余力チェック =====
    LOW_AVAIL_RATIO = 0.30  # 30%
    negatives, low_avails = [], []

    for b in brokers:
        avail = float(b.get("available", 0))
        cash = float(b.get("cash", 0))
        br = b.get("broker", "")
        # マイナス
        if avail < 0:
            negatives.append((br, int(avail)))
        # 残30%未満（マイナス以外）
        elif cash > 0 and avail / cash < LOW_AVAIL_RATIO:
            pct = round(avail / cash * 100, 1)
            low_avails.append((br, int(avail), pct))

    # --- マイナスの警告 ---
    if negatives:
        details = "\n".join([f"・{br}：{val:,} 円" for br, val in negatives])
        messages.warning(
            request,
            f"⚠️ 余力がマイナスの証券口座があります！\n{details}\n入出金や拘束、保有残高を確認してください。"
        )

    # --- 残30%未満の警告 ---
    if low_avails:
        lines = [f"・{br}：余力 {pct:.1f}%（残り {av:,} 円）" for br, av, pct in low_avails]
        msg = (
            "⚠️ 余力が少なくなっています！\n"
            + "\n".join(lines)
            + "\n入金やポジション整理を検討してください。"
        )
        messages.warning(request, msg)

    return render(request, "cash/dashboard.html", {
        "brokers": brokers,
        "kpi_total": kpi_total,
    })