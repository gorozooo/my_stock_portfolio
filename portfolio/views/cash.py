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


# ================== helpers ==================
def _get_account(broker: str, currency: str = "JPY") -> BrokerAccount | None:
    svc.ensure_default_accounts(currency=currency)
    return (
        BrokerAccount.objects.filter(broker=broker, currency=currency)
        .order_by("account_type")
        .first()
    )


def _severity_for(b: dict, low_ratio: float = 0.30) -> str:
    """
    表示用の危険度:
      - 'danger' : 余力 < 0
      - 'warn'   : 余力/預り金 < low_ratio (預り金>0 の時のみ)
      - 'ok'     : それ以外
    """
    avail = int(b.get("available", 0))
    cash  = int(b.get("cash", 0))
    if avail < 0:
        return "danger"
    if cash > 0 and (avail / cash) < low_ratio:
        return "warn"
    return "ok"


def _format_int(n: int) -> str:
    return f"{n:,}"


def _make_negative_toast(negatives: list[tuple[str, int]]) -> str:
    lines = ["⚠️ 余力がマイナスの証券口座があります！"]
    for br, val in negatives:
        lines.append(f"・{br}：{_format_int(val)} 円")
    lines.append("入出金や拘束、保有残高を確認してください。")
    return "\n".join(lines)


def _make_low_toast(lows: list[tuple[str, int, int, float]]) -> str:
    # (broker, avail, cash, pct)
    lines = ["⚠️ 余力が少なくなっています！"]
    for br, avail, cash, pct in lows:
        lines.append(
            f"・{br}：余力 {pct:.1f}%（残り {_format_int(avail)} 円）"
        )
    lines.append("入金やポジション整理を検討してください。")
    return "\n".join(lines)


# ================== dashboard ==================
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

        # transfer は UI から消しているが、不正 POST へのガード
        if op == "transfer":
            messages.error(request, "振替は現在サポートしていません。")
            return redirect("cash_dashboard")

        messages.error(request, "不正な操作が指定されました。")
        return redirect("cash_dashboard")

    # ====== GET ======
    svc.ensure_default_accounts()
    today = date.today()

    # 同期（失敗しても画面は表示）
    try:
        info = up.sync_all()
        d = int(info.get("dividends_created", 0))
        r = int(info.get("realized_created", 0))
        if d or r or request.GET.get("force_toast") == "1":
            messages.info(request, f"同期完了\n・配当：新規 {d} 件\n・実損：新規 {r} 件")
    except Exception as e:
        messages.error(request, f"同期に失敗：{e}")

    # === 集計 ===
    # svc.broker_summaries(today) => [{broker, cash, restricted, available, month_net}]
    base_list = svc.broker_summaries(today)

    # しきい値（%）
    LOW_RATIO = 0.30

    # ビュー側で“テンプレで使う値は全部”作る
    enhanced = []
    lows_for_toast: list[tuple[str, int, int, float]] = []
    neg_for_toast:  list[tuple[str, int]] = []

    for row in base_list:
        broker = row.get("broker", "")
        cash   = int(row.get("cash", 0))
        avail  = int(row.get("available", 0))
        restr  = int(row.get("restricted", 0))
        month_net = int(row.get("month_net", 0))

        pct = (avail / cash * 100.0) if cash > 0 else None
        severity = _severity_for(row, LOW_RATIO)

        # トースト用の収集
        if avail < 0:
            neg_for_toast.append((broker, avail))
        elif cash > 0 and (avail / cash) < LOW_RATIO:
            lows_for_toast.append((broker, avail, cash, (avail / cash) * 100.0))

        enhanced.append({
            "broker": broker,
            "cash": cash,
            "available": avail,
            "restricted": restr,
            "month_net": month_net,
            "pct_available": pct,   # float | None
            "severity": severity,   # 'danger' | 'warn' | 'ok'
        })

    # 警告トースト（両方表示）
    if neg_for_toast:
        messages.error(request, _make_negative_toast(neg_for_toast))
    if lows_for_toast:
        messages.warning(request, _make_low_toast(lows_for_toast))

    # KPI 合計は既存の total_summary をそのまま使用
    kpi_total, _ = svc.total_summary(today)

    return render(
        request,
        "cash/dashboard.html",
        {
            "brokers": enhanced,      # テンプレはこの“完成形”だけを使う
            "kpi_total": kpi_total,
        },
    )