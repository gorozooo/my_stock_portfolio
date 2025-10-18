# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone as dt_tz
from typing import List, Dict, Any

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.db.models import Count, Q, F
from django.utils import timezone

from ..models_advisor import AdviceItem
from django.conf import settings
from pathlib import Path

# --- policy.json 読み取り（整形して表示用に短くする） ---
def _policy_path() -> Path:
    rel = getattr(settings, "ADVISOR_POLICY_PATH", "media/advisor/policy.json")
    if Path(rel).is_absolute():
        return Path(rel)
    base = Path(getattr(settings, "MEDIA_ROOT", "") or Path.cwd())
    return (base / rel).resolve()

def _read_policy_preview() -> str:
    p = _policy_path()
    if not p.exists():
        return "{}"
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        # 表示は必要最小限のキーに絞って整形
        keys = ("rs_thresholds", "notify_thresholds", "window_days", "updated_at")
        snap = {k: obj.get(k) for k in keys}
        # どちらも空dictのときに {} にならないよう、キー自体は残す
        if snap.get("updated_at") is None and obj.get("updated_at"):
            snap["updated_at"] = obj["updated_at"]
        return json.dumps(snap, ensure_ascii=False, indent=2)
    except Exception:
        return "{}"

def notify_dashboard(request: HttpRequest) -> HttpResponse:
    """
    通知ダッシュボード（HTML/JSON）
    - ?format=json でJSONを返す
    - days（デフォルト90）
    """
    days = int(request.GET.get("days", 90))
    since = timezone.now() - timedelta(days=days)

    # 今週（Monスタート）
    now = timezone.localtime()
    monday = now - timedelta(days=(now.weekday()))
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

    # 通知は AdviceItem を「通知ログ」として集計（taken が採用）
    qs_all = AdviceItem.objects.filter(created_at__gte=since)
    week_qs = qs_all.filter(created_at__gte=monday)

    week_total = week_qs.count()
    week_taken = week_qs.filter(taken=True).count()
    week_rate = (week_taken / week_total) if week_total > 0 else 0.0

    # 週ごとの集計（直近数週）
    # PostgreSQLでもSQLiteでも動くように、週頭（月曜）をPython側で丸める
    weekly: List[Dict[str, Any]] = []
    cursor = monday
    # 12週ぶんさかのぼる（必要に応じて調整）
    for i in range(12):
        start = (monday - timedelta(weeks=i))
        end = start + timedelta(days=7)
        w_qs = qs_all.filter(created_at__gte=start, created_at__lt=end)
        total = w_qs.count()
        taken = w_qs.filter(taken=True).count()
        rate = (taken / total) if total > 0 else 0.0
        weekly.append({
            "week": start.date().isoformat(),
            "total": total,
            "taken": taken,
            "rate": round(rate, 4),
        })
    weekly.sort(key=lambda r: r["week"], reverse=True)

    # policy プレビュー
    policy_preview = _read_policy_preview()

    # JSON
    if request.GET.get("format") == "json":
        return JsonResponse({
            "days": days,
            "week_total": week_total,
            "week_taken": week_taken,
            "week_rate": round(week_rate, 4),
            "weekly": weekly,
            "policy": json.loads(policy_preview or "{}"),
        }, json_dumps_params={"ensure_ascii": False, "indent": 2})

    # HTML
    ctx = {
        "days": days,
        "week_total": week_total,
        "week_taken": week_taken,
        "week_rate": week_rate,
        "weekly": weekly,
        "policy_preview": policy_preview,  # 整形済みテキスト
    }
    return render(request, "advisor/notify_dashboard.html", ctx)