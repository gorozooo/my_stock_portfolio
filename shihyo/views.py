"""
[FILE] views.py
[PATH] <project_root>/shihyo/views.py

このファイルは何？
- 指標専用ダッシュボード（iPhone向け1画面）を表示するViewです。
- 最新スナップショットを1件だけ表示します。
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from shihyo.models import MarketIndicatorSnapshot


@login_required
def dashboard(request):
    latest = MarketIndicatorSnapshot.objects.first()
    return render(request, "shihyo/dashboard.html", {"latest": latest})