# portfolio/views/autopilot.py
from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def autopilot_page(request):
    """
    AIオートパイロット画面（学習/スコア/計画登録）
    既存の api/metrics, api/ohlc, advisor/policy, advisor/learn をフロントから叩きます。
    """
    return render(request, "autopilot/index.html", {})