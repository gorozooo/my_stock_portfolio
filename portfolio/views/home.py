# portfolio/views/home.py
from __future__ import annotations

import logging
from typing import Any, Dict, List

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone


logger = logging.getLogger(__name__)


def _safe_localdate_str() -> str:
    """
    例: 2025年12月29日(月)
    """
    try:
        d = timezone.localdate()
    except Exception:
        d = timezone.now().date()
    wd = ["月", "火", "水", "木", "金", "土", "日"][d.weekday()]
    return f"{d.year}年{d.month:02d}月{d.day:02d}日({wd})"


def _build_ai_brief(user) -> Dict[str, Any]:
    return {
        "title": "AI BRIEF",
        "status": "stub",
        "headline": "（準備中）",
        "bullets": [],
    }


def _build_risk(user) -> Dict[str, Any]:
    return {
        "title": "RISK",
        "status": "stub",
        "metrics": [],
        "alerts": [],
    }


def _build_market() -> Dict[str, Any]:
    return {
        "title": "MARKET",
        "status": "stub",
        "summary": "（準備中）",
        "items": [],
    }


def _build_today_plan_from_assets(assets: Dict[str, Any]) -> Dict[str, Any]:
    """
    TODAY PLAN を assets(pace) から自動生成
    - ここは “意思決定支援” の入り口。あとで Policy/Watch/OrderMemo と接続予定。
    """
    try:
        pace = (assets or {}).get("pace") or {}
        total_m = (pace.get("total_need_per_month") or {})
        total_w = (pace.get("total_need_per_week") or {})
        by = pace.get("by_broker_rows") or []

        rem_month = float(total_m.get("remaining") or 0.0)
        need_m = float(total_m.get("need_per_slot") or 0.0)
        need_w = float(total_w.get("need_per_slot") or 0.0)

        tasks: List[Dict[str, Any]] = []

        if rem_month > 0:
            tasks.append({
                "kind": "primary",
                "title": "今月の必要ペースを意識",
                "desc": f"今月：残り {int(rem_month):,} 円 → 月あたり {int(need_m):,} 円 / 週あたり {int(need_w):,} 円",
            })

            # broker別：月ペースの必要額が大きい順
            def keyf(r):
                p = (r.get("pace_month") or {}).get("need_per_slot") or 0.0
                try:
                    return float(p)
                except Exception:
                    return 0.0

            by_sorted = sorted(by, key=keyf, reverse=True)
            top = by_sorted[:2]

            for r in top:
                pm = r.get("pace_month") or {}
                need_b = float(pm.get("need_per_slot") or 0.0)
                rem_b = float(pm.get("remaining") or 0.0)
                tasks.append({
                    "kind": "broker",
                    "title": f"{r.get('label','')} を優先",
                    "desc": f"残り {int(rem_b):,} 円 → 月 {int(need_b):,} 円ペース",
                })

            tasks.append({
                "kind": "check",
                "title": "無理に増やさず、ルールで回す",
                "desc": "損切り/利確ルール優先。取り返しトレード禁止（監視→機械的に）。",
            })
        else:
            # 達成済（または目標0で rem=0）
            tasks.append({
                "kind": "ok",
                "title": "目標ペース上は問題なし",
                "desc": "無理に利益を積むより、崩さない運用（再現性・ポリシー順守）を優先。",
            })
            tasks.append({
                "kind": "check",
                "title": "やることは“減らす”方向",
                "desc": "無駄なエントリーを減らす。監視・仕込み・記録の精度を上げる。",
            })

        return {
            "title": "TODAY PLAN",
            "status": "ok",
            "tasks": tasks,
        }
    except Exception as e:
        logger.exception("TODAY PLAN build failed: %s", e)
        return {
            "title": "TODAY PLAN",
            "status": "error",
            "tasks": [],
            "error": str(e),
        }


def _build_news_trends() -> Dict[str, Any]:
    try:
        from aiapp.services.home_news_trends import get_news_trends_snapshot  # type: ignore
        snap = get_news_trends_snapshot()
        if not isinstance(snap, dict):
            return {"status": "error", "error": "news snapshot is not dict", "items": []}
        snap.setdefault("status", "ok")
        snap.setdefault("items", [])
        snap.setdefault("sectors", [])
        snap.setdefault("trends", [])
        snap.setdefault("as_of", timezone.now().isoformat())
        return snap
    except Exception as e:
        logger.exception("NEWS & TRENDS build failed: %s", e)
        return {
            "status": "stub",
            "as_of": timezone.now().isoformat(),
            "items": [],
            "sectors": [],
            "trends": [],
            "error": str(e),
        }


@login_required
def home(request):
    """
    Home = デッキ（横スワイプ）前提
    - デッキ順：ASSETS → AI BRIEF → RISK → MARKET → TODAY PLAN → NEWS & TRENDS
    """
    # --- ASSETS（リアルタイム） ---
    try:
        from ..services.home_assets import build_assets_snapshot
        assets = build_assets_snapshot(request.user)
        if not isinstance(assets, dict):
            assets = {"status": "error", "error": "assets snapshot is not dict"}
        assets.setdefault("status", "ok")
    except Exception as e:
        logger.exception("ASSETS build failed: %s", e)
        assets = {"status": "error", "error": str(e)}

    # --- NEWS & TRENDS（5分TTL） ---
    news_trends = _build_news_trends()

    # --- その他 ---
    ai_brief = _build_ai_brief(request.user)
    risk = _build_risk(request.user)
    market = _build_market()

    # ★ TODAY PLAN は assets から生成（ここが今回の要）
    today_plan = _build_today_plan_from_assets(assets)

    decks: List[Dict[str, Any]] = [
        {"key": "assets", "title": "ASSETS", "payload": assets},
        {"key": "ai_brief", "title": "AI BRIEF", "payload": ai_brief},
        {"key": "risk", "title": "RISK", "payload": risk},
        {"key": "market", "title": "MARKET", "payload": market},
        {"key": "today_plan", "title": "TODAY PLAN", "payload": today_plan},
        {"key": "news_trends", "title": "NEWS & TRENDS", "payload": news_trends},
    ]

    context = {
        "today_label": _safe_localdate_str(),
        "decks": decks,
        "enable_detail_links": False,
    }
    return render(request, "home.html", context)