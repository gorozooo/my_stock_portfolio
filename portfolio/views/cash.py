# portfolio/views/cash.py
from __future__ import annotations
from django.shortcuts import render
from django.http import HttpRequest, HttpResponse

def cash_dashboard(request: HttpRequest) -> HttpResponse:
    """
    💵 現金ダッシュボード（MVP）
    まずはページが開くことを最優先。後でKPI/台帳/口座カードを差し込む。
    """
    # ここに後で集計ロジックを入れる想定。暫定のダミー値。
    context = {
        "kpi": {
            "available": 0,     # 総余力
            "cash_total": 0,    # 総預り金
            "restricted": 0,    # 総拘束
            "month_net": 0,     # 当月入出金差分
        },
        "brokers": [],          # 口座カード用（SBI/Rakuten…）
        "action": request.GET.get("action", ""),  # ?action=deposit 等のプレースホルダ
    }
    return render(request, "cash/dashboard.html", context)