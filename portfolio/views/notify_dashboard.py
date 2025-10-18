# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from datetime import timedelta
from typing import List, Dict, Any
from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.conf import settings

from ..models_advisor import AdviceItem
from ..models import Holding
from ..services.sector_map import normalize_sector
from ..services.market import latest_sector_strength


# ---------- policy.json 読み取り ----------
def _policy_path() -> Path:
    rel = getattr(settings, "ADVISOR_POLICY_PATH", "media/advisor/policy.json")
    if Path(rel).is_absolute():
        return Path(rel)
    base = Path(getattr(settings, "MEDIA_ROOT", "") or Path.cwd())
    return (base / rel).resolve()

def _read_policy_obj() -> dict:
    p = _policy_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _read_policy_preview() -> dict:
    """
    policy.jsonをサマリ化して辞書で返す
    """
    obj = _read_policy_obj() or {}
    notify = obj.get("notify_thresholds") or {}
    out = dict(
        rs_weak=notify.get("rs_weak"),
        rs_strong=notify.get("rs_strong"),
        gap_min=notify.get("gap_min"),
        liq_max=notify.get("liq_max"),
        margin_min=notify.get("margin_min"),
        top_share_max=notify.get("top_share_max"),
        uncat_share_max=notify.get("uncat_share_max"),
        breadth_bad=notify.get("breadth_bad"),
        breadth_good=notify.get("breadth_good"),
        updated_at=obj.get("updated_at"),
    )
    return out


# ---------- セクター集計 ----------
@dataclass
class SectorRow:
    sector: str
    weight_pct: float
    mv: float
    rs: float
    rs_date: str | None

def _sf(x, d: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return d

def _holdings_by_sector() -> tuple[list[dict], float]:
    """
    保有をセクター別に評価額集計。
    last_price 優先（なければ avg_cost）
    """
    rows = []
    for h in Holding.objects.all():
        qty = _sf(getattr(h, "quantity", 0.0))
        unit = _sf(getattr(h, "avg_cost", 0.0))
        price = _sf(getattr(h, "last_price", None)) or unit
        mv = max(0.0, qty * price)
        sec = normalize_sector((getattr(h, "sector", "") or "").strip() or "未分類")
        rows.append((sec, mv))

    by = defaultdict(float)
    for sec, mv in rows:
        by[sec] += mv

    listed = [{"sector": k, "mv": v} for k, v in by.items()]
    listed.sort(key=lambda r: r["mv"], reverse=True)
    total_mv = sum(r["mv"] for r in listed) or 1.0
    for r in listed:
        r["rate"] = r["mv"] / total_mv * 100.0
    return listed, total_mv

def _join_with_rs(pf_rows: list[dict]) -> list[SectorRow]:
    rs_tbl = latest_sector_strength() or {}
    out: list[SectorRow] = []
    for r in pf_rows:
        sec = r["sector"]
        rs_row = rs_tbl.get(sec) or {}
        rs = _sf(rs_row.get("rs_score"), 0.0)
        out.append(SectorRow(
            sector=sec,
            weight_pct=_sf(r.get("rate"), 0.0),
            mv=_sf(r.get("mv"), 0.0),
            rs=rs,
            rs_date=(rs_row.get("date") or None),
        ))
    out.sort(key=lambda x: (-(x.weight_pct), -x.rs))
    return out


# ---------- メイン ----------
def notify_dashboard(request: HttpRequest) -> HttpResponse:
    """
    AIアドバイザー：通知＋セクター＋しきい値の統合ダッシュボード
    - ?format=json でJSON返却
    - days パラメータで期間指定（default=90）
    """
    days = int(request.GET.get("days", 90))
    since = timezone.now() - timedelta(days=days)

    # 今週（月曜始まり）
    now = timezone.localtime()
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

    # 通知（AdviceItem）集計
    qs_all = AdviceItem.objects.filter(created_at__gte=since)
    week_qs = qs_all.filter(created_at__gte=monday)
    week_total = week_qs.count()
    week_taken = week_qs.filter(taken=True).count()
    week_rate = (week_taken / week_total) if week_total > 0 else 0.0

    # 週次トレンド（直近12週）
    weekly: List[Dict[str, Any]] = []
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

    # セクター × RS
    pf_rows, total_mv = _holdings_by_sector()
    sector_rows = _join_with_rs(pf_rows)
    max_w = max((r.weight_pct for r in sector_rows), default=0.0)

    # policy サマリ
    policy_summary = _read_policy_preview()

    # JSONモード
    if request.GET.get("format") == "json":
        return JsonResponse({
            "days": days,
            "week_total": week_total,
            "week_taken": week_taken,
            "week_rate": round(week_rate, 4),
            "weekly": weekly,
            "sectors": [
                {"sector": r.sector, "weight_pct": r.weight_pct, "mv": r.mv, "rs": r.rs, "rs_date": r.rs_date}
                for r in sector_rows
            ],
            "policy": policy_summary,
        }, json_dumps_params={"ensure_ascii": False, "indent": 2})

    # HTML
    ctx = dict(
        days=days,
        week_total=week_total,
        week_taken=week_taken,
        week_rate=week_rate,
        weekly=weekly,
        sector_rows=sector_rows,
        total_mv=total_mv,
        max_w=max_w,
        policy_text=policy_summary,
        now=now,
    )
    return render(request, "portfolio/notify_dashboard.html", ctx)