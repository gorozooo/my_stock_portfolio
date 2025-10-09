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

    # ========== GET ==========
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

    # === 集計 ===
    brokers = svc.broker_summaries(today)
    kpi_total, _ = svc.total_summary(today)

    # === 余力アラート ===
    # 1) マイナス → エラートースト（赤）
    negatives = [(b["broker"], b.get("available", 0)) for b in brokers if b.get("available", 0) < 0]
    if negatives:
        detail = "\n".join([f"・{br}：{val:,} 円" for br, val in negatives])
        messages.error(
            request,
            "余力がマイナスの証券口座があります！\n"
            f"{detail}\n"
            "入出金や拘束、保有残高を確認してください。"
        )

    # 2) 余力が「預り金の30%未満」 → 注意トースト（黄）
    THRESH = 30.0
    lows = []
    for b in brokers:
        cash = float(b.get("cash", 0) or 0)
        avail = float(b.get("available", 0) or 0)
        if cash <= 0 or avail < 0:
            continue  # 0割り/マイナスは上のエラーで扱う
        pct = (avail / cash) * 100.0
        if pct < THRESH:
            lows.append((b["broker"], pct, int(avail)))

    if lows and not negatives:
        lines = []
        for br, pct, av in lows:
            # 例: ・松井：余力 22%（残り 163,012 円）
            pct_int = int(round(pct))
            lines.append(f"・{br}：余力 {pct_int}%（残り {av:,} 円）")
        body = "\n".join(lines)
        messages.warning(
            request,
            "余力が少なくなっています！\n"
            f"{body}\n"
            "入金やポジション整理を検討してください。"
        )

    return render(request, "cash/dashboard.html", {
        "brokers": brokers,
        "kpi_total": kpi_total,
    })