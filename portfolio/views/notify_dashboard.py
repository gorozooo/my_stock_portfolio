# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from datetime import timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

from django.conf import settings
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from ..models_advisor import AdviceItem

# =========================
# policy.json 読み取り
# =========================
def _policy_path() -> Path:
    """
    MEDIA_ROOT 基準 or 絶対パスの ADVISOR_POLICY_PATH を解決。
    既定: media/advisor/policy.json
    """
    rel = getattr(settings, "ADVISOR_POLICY_PATH", "media/advisor/policy.json")
    p = Path(rel)
    if p.is_absolute():
        return p
    base = Path(getattr(settings, "MEDIA_ROOT", "") or Path.cwd())
    return (base / rel).resolve()

def _read_policy_obj() -> Optional[dict]:
    """
    policy.json を dict で返す。無ければ None。
    """
    path = _policy_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _read_policy_preview(obj: Optional[dict]) -> str:
    """
    画面に出す軽量サマリ（JSON文字列）。
    """
    if not obj:
        return "{}"
    try:
        keys = ("rs_thresholds", "notify_thresholds", "window_days", "updated_at")
        snap = {k: obj.get(k) for k in keys}
        return json.dumps(snap, ensure_ascii=False, indent=2)
    except Exception:
        return "{}"

# =========================
# メインビュー
# =========================
def notify_dashboard(request: HttpRequest) -> HttpResponse:
    """
    通知ダッシュボード（HTML / JSON）
      - ?format=json で JSON を返す
      - days（デフォルト 90）
    """
    days = int(request.GET.get("days", 90))
    since = timezone.now() - timedelta(days=days)

    # 今週（月曜スタート）の境界
    now = timezone.localtime()
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

    # 通知ログは AdviceItem（taken=True が採用）
    qs_all = AdviceItem.objects.filter(created_at__gte=since)
    week_qs = qs_all.filter(created_at__gte=monday)

    week_total = week_qs.count()
    week_taken = week_qs.filter(taken=True).count()
    week_rate = (week_taken / week_total) if week_total > 0 else 0.0

    # 直近12週の集計（Mon〜Sun）
    weekly: List[Dict[str, Any]] = []
    for i in range(12):
        start = monday - timedelta(weeks=i)
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
    weekly_max = max((row["total"] for row in weekly), default=0)

    # policy
    policy_obj = _read_policy_obj()            # dict（テンプレでチップ表示に使用）
    policy_preview = _read_policy_preview(policy_obj)  # 折りたたみ RAW 表示用

    # JSON 応答
    if request.GET.get("format") == "json":
        return JsonResponse({
            "days": days,
            "week_total": week_total,
            "week_taken": week_taken,
            "week_rate": round(week_rate, 4),
            "weekly": weekly,
            "policy": (policy_obj or {}),
        }, json_dumps_params={"ensure_ascii": False, "indent": 2})

    # HTML 応答
    ctx = {
        "days": days,
        "week_total": week_total,
        "week_taken": week_taken,
        "week_rate": week_rate,
        "weekly": weekly,
        "weekly_max": weekly_max or None,
        "policy": policy_obj,               # ← dict を直接渡す（チップが '—' でフォールバック表示）
        "policy_preview": policy_preview,   # ← RAW 折りたたみ用テキスト
    }
    # テンプレートは advisor/notify_dashboard.html を想定
    return render(request, "portfolio/notify_dashboard.html", ctx)