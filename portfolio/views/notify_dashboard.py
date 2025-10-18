# -*- coding: utf-8 -*-
from __future__ import annotations
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from ..services.advisor_metrics import weekly_notify_stats, current_thresholds, latest_week_summary

def notify_dashboard(request: HttpRequest) -> HttpResponse:
    days = int(request.GET.get("days", 90))
    fmt = request.GET.get("format", "html")
    stats = weekly_notify_stats(days=days)
    thr = current_thresholds()
    head = latest_week_summary(days=days)

    payload = {
        "stats": stats,
        "thresholds": thr,
        "headline": head,
        "days": days,
    }
    if fmt == "json":
        return JsonResponse(payload, json_dumps_params={"ensure_ascii": False, "indent": 2})
    return render(request, "portfolio/notify_dashboard.html", payload)