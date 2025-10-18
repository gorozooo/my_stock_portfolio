# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, List, Tuple
from datetime import timedelta
import os, json

from django.utils import timezone
from django.conf import settings
from django.db.models.functions import TruncWeek
from django.db.models import Count, Q

from ..models_advisor import AdviceItem


def _policy_path() -> str:
    rel = getattr(settings, "ADVISOR_POLICY_PATH", "media/advisor/policy.json")
    if os.path.isabs(rel):
        return rel
    base = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    return os.path.join(base, rel)

def load_policy_blob() -> Dict[str, Any]:
    path = _policy_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}

def current_thresholds() -> Dict[str, Any]:
    pol = load_policy_blob()
    return {
        "rs_thresholds": (pol.get("rs_thresholds") or {}),
        "notify_thresholds": (pol.get("notify_thresholds") or {}),
        "window_days": pol.get("window_days"),
        "updated_at": pol.get("updated_at"),
    }

def weekly_notify_stats(days: int = 90) -> List[Dict[str, Any]]:
    """
    過去N日ぶんの AdviceItem を週単位で集計
    - total: 生成件数
    - taken: 採用件数
    - take_rate: 採用率
    """
    since = timezone.now() - timedelta(days=days)
    qs = (AdviceItem.objects
          .filter(created_at__gte=since)
          .annotate(week=TruncWeek("created_at"))
          .values("week")
          .annotate(total=Count("id"),
                    taken=Count("id", filter=Q(taken=True)))
          .order_by("week"))

    out: List[Dict[str, Any]] = []
    for row in qs:
        total = int(row["total"] or 0)
        taken = int(row["taken"] or 0)
        take_rate = (taken / total) if total > 0 else 0.0
        out.append({
            "week": row["week"].date().isoformat() if row["week"] else None,
            "total": total,
            "taken": taken,
            "take_rate": round(take_rate, 4),
        })
    return out

def latest_week_summary(days: int = 90) -> Dict[str, Any]:
    stats = weekly_notify_stats(days=days)
    if not stats:
        return {"per_week": 0.0, "take_rate": 0.0, "weeks": 0}
    per_week = stats[-1]["total"]
    take_rate = stats[-1]["take_rate"]
    return {"per_week": per_week, "take_rate": take_rate, "weeks": len(stats)}