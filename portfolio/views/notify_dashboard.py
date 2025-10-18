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

def _read_policy_preview() -> str:
    obj = _read_policy_obj() or {}
    snap = {
        "rs_thresholds": obj.get("rs_thresholds") or {"weak": -0.3, "strong": 0.4},
        "notify_thresholds": obj.get("notify_thresholds") or {
            "gap_min": 22.0,
            "liq_max": 48.0,
            "margin_min": 62.0,
            "top_share_max": 47.0,
            "uncat_share_max": 42.0,
            "breadth_bad": -0.4,
            "breadth_good": 0.4
        },
        "window_days": obj.get("window_days") or 90,
        "updated_at": obj.get("updated_at")
    }
    return json.dumps(snap, ensure_ascii=False, indent=2)

def _policy_text_summary() -> Dict[str, Any]:
    """
    policy.json から UI 表示用のサマリ辞書を作る。
    - 数値は小数2桁に整形（文字列）。欠損は None。
    """
    pol = _read_policy_obj() or {}
    rs = pol.get("rs_thresholds") or {}
    nt = pol.get("notify_thresholds") or {}

    return {
        # RS（相対強弱）
        "rs_weak": _fmt(rs.get("weak")),
        "rs_strong": _fmt(rs.get("strong")),
        # 通知トリガー（評価指標）
        "gap_min": _fmt(nt.get("gap_min")),
        "liq_max": _fmt(nt.get("liq_max")),
        "margin_min": _fmt(nt.get("margin_min")),
        # 構成（セクター制約）
        "top_share_max": _fmt(nt.get("top_share_max")),
        "uncat_share_max": _fmt(nt.get("uncat_share_max")),
        # 地合い（ブレッドス）
        "breadth_bad": _fmt(nt.get("breadth_bad")),
        "breadth_good": _fmt(nt.get("breadth_good")),
        # 参考情報
        "window_days": pol.get("window_days"),
        "updated_at": pol.get("updated_at"),
    }

def _get_rs_thresholds() -> tuple[float, float]:
    """policy.json の rs_thresholds（弱/強）を取り出す。無ければデフォルト。"""
    pol = _read_policy_obj() or {}
    th = pol.get("rs_thresholds") or {}
    try:
        weak = float(th.get("weak"))
        strong = float(th.get("strong"))
        if weak < strong:
            return weak, strong
    except Exception:
        pass
    return (-0.25, 0.35)


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
    保有をセクター別に評価額集計。last_price 優先（無ければ avg_cost）。
    返り値: ( [{"sector":..., "mv":..., "rate":...}], total_mv )
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

def _rs_level(rs: float, weak: float, strong: float) -> tuple[str, str]:
    """RSの水準をバッジ表示用に分類。返り値: (表示テキスト, CSSクラス)"""
    try:
        r = float(rs)
    except Exception:
        r = 0.0
    if r <= weak:
        return ("弱", "weak")
    if r >= strong:
        return ("強", "strong")
    return ("中立", "neutral")


# ---------- メイン ----------
def notify_dashboard(request: HttpRequest) -> HttpResponse:
    """
    AIアドバイザー：通知＋セクター＋しきい値の統合ページ
    - ?format=json でサマリJSON（後方互換）
    - days パラメータ（default 90）
    """
    days = int(request.GET.get("days", 90))
    since = timezone.now() - timedelta(days=days)

    # 今週（月曜0:00起点）
    now = timezone.localtime()
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)

    # 通知集計（AdviceItem を通知ログとみなす / taken=True は「採用」）
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
        tot = w_qs.count()
        tak = w_qs.filter(taken=True).count()
        rate = (tak / tot) if tot > 0 else 0.0
        weekly.append({
            "week": start.date().isoformat(),
            "total": tot,
            "taken": tak,
            "rate": round(rate, 4),
        })
    weekly.sort(key=lambda r: r["week"], reverse=True)

    # セクター：PFウェイト × RS
    pf_rows, total_mv = _holdings_by_sector()
    sector_rows = _join_with_rs(pf_rows)
    max_w = max((r.weight_pct for r in sector_rows), default=0.0)

    rs_weak, rs_strong = _get_rs_thresholds()
    # テンプレに渡す見やすい行（バッジ用のlevel/class、バー幅など）
    sector_rows_viz = []
    for r in sector_rows:
        level_txt, level_cls = _rs_level(r.rs, rs_weak, rs_strong)
        # ウェイトのバー幅（最大ウェイト基準で100%スケール）
        weight_bar = 0.0 if max_w <= 0 else (r.weight_pct / max_w) * 100.0
        # RSを -1..+1 → 0..100 に正規化（オーバーはclip）
        rs_norm = max(0.0, min(100.0, (r.rs + 1.0) * 50.0))
        sector_rows_viz.append({
            "sector": r.sector,
            "weight_pct": round(r.weight_pct, 2),
            "weight_bar": round(weight_bar, 1),
            "mv": round(r.mv, 0),
            "rs": round(r.rs, 2),
            "rs_norm": round(rs_norm, 1),
            "level_txt": level_txt,
            "level_cls": level_cls,
        })

    # policy
    policy_preview = _read_policy_preview()
    policy_text = _policy_text_summary()

    # JSON（後方互換）
    if request.GET.get("format") == "json":
        return JsonResponse({
            "days": days,
            "week_total": week_total,
            "week_taken": week_taken,
            "week_rate": round(week_rate, 4),
            "weekly": weekly,
            "policy": json.loads(policy_preview or "{}"),
            "policy_text": policy_text,
            "sectors": sector_rows_viz,
            "rs_thresholds": {"weak": rs_weak, "strong": rs_strong},
        }, json_dumps_params={"ensure_ascii": False, "indent": 2})

    # HTML
    ctx = dict(
        days=days,
        week_total=week_total,
        week_taken=week_taken,
        week_rate=week_rate,
        weekly=weekly,
        policy_preview=policy_preview,  # RAW表示用の整形JSON文字列
        policy_text=policy_text,        # カード表示用のフラットな辞書
        sectors=sector_rows_viz,        # セクター（可視化用）
        total_mv=total_mv,
        rs_weak=rs_weak,
        rs_strong=rs_strong,
        now=now,
    )
    return render(request, "portfolio/notify_dashboard.html", ctx)