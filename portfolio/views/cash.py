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

        # 口座間振替（UIは現状無しだが互換のため残す）
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
    brokers = svc.broker_summaries(today)  # broker, cash, restricted, available, month_net
    kpi_total, account_rows = svc.total_summary(today)  # account_rows は account_summary() の行

    # ブローカー単位の内訳（取得原価残・担保可能額を合算）
    details_by_broker: dict[str, dict] = {}
    for r in account_rows:
        b = r["broker"]
        d = details_by_broker.setdefault(
            b,
            {"invested_cost": 0, "collateral_usable": 0, "cash": 0, "restricted": 0, "available": 0},
        )
        d["invested_cost"] += int(r.get("invested_cost", 0))
        d["collateral_usable"] += int(r.get("collateral_usable", 0))
        d["cash"] += int(r.get("cash", 0))
        d["restricted"] += int(r.get("restricted", 0))
        d["available"] += int(r.get("available", 0))

    # --- アラート（マイナス & 30%未満） ---
    # 基準：available / max(cash + collateral, 1)
    THRESHOLD_PCT = 30

    negatives = []
    lows = []
    for b in brokers:
        broker = b["broker"]
        available = int(b.get("available", 0))
        cash = int(details_by_broker.get(broker, {}).get("cash", b.get("cash", 0)))
        coll = int(details_by_broker.get(broker, {}).get("collateral_usable", 0))
        denom = max(cash + coll, 1)
        pct = int(round(available / denom * 100)) if denom > 0 else 0

        if available < 0:
            negatives.append((broker, available))
        elif available >= 0 and pct < THRESHOLD_PCT:
            lows.append((broker, pct, available))

    # マイナス（赤トースト）
    if negatives:
        lines = [f"・{br}：{val:,} 円" for br, val in negatives]
        msg = "⚠️ 余力がマイナスの証券口座があります！\n" + "\n".join(lines) + "\n入出金や拘束、保有残高を確認してください。"
        messages.error(request, msg)

    # 30%未満（黄トースト）
    if lows:
        lines = [f"・{br}：余力 {pct}%（残り {avail:,} 円）" for br, pct, avail in lows]
        msg = "⚠️ 余力が少なくなっています！\n" + "\n".join(lines) + "\n入金やポジション整理を検討してください。"
        messages.warning(request, msg)

    return render(
        request,
        "cash/dashboard.html",
        {
            "brokers": brokers,
            "kpi_total": kpi_total,
            "details_by_broker": details_by_broker,
            "threshold_pct": THRESHOLD_PCT,
        },
    )