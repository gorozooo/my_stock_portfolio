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
    """
    AI BRIEF（短い朝の要約）
    - まだ専用サービスが無いので、今は器だけ作る
    - 後で aiapp 側の「AIコメント生成」や「macro regime」等につなぐ
    """
    return {
        "title": "AI BRIEF",
        "status": "stub",
        "headline": "（準備中）",
        "bullets": [],
    }


def _build_risk(user) -> Dict[str, Any]:
    """
    RISK（リスク状況）
    - 将来：信用余力/拘束/レバ、ポリシー逸脱、想定最大DDなど
    """
    return {
        "title": "RISK",
        "status": "stub",
        "metrics": [],
        "alerts": [],
    }


def _build_market() -> Dict[str, Any]:
    """
    MARKET（指数・ブレッドス・セクター）
    - 将来：indexes_update_auto / breadth_update_auto / sector_update_auto の成果物を参照
    """
    return {
        "title": "MARKET",
        "status": "stub",
        "summary": "（準備中）",
        "items": [],
    }


def _build_today_plan(user) -> Dict[str, Any]:
    """
    TODAY PLAN（今日やること）
    - 将来：AdvisorPolicy / Watch / 条件達成 / 発注メモ / 行動ログと統合
    """
    return {
        "title": "TODAY PLAN",
        "status": "stub",
        "tasks": [],
    }


def _build_news_trends() -> Dict[str, Any]:
    """
    NEWS & TRENDS（5分TTL想定）
    - aiapp/services/home_news_trends.py の get_news_trends_snapshot() が本体
    - まだ未実装でも Home が落ちないようにガード
    """
    try:
        from aiapp.services.home_news_trends import get_news_trends_snapshot  # type: ignore
        snap = get_news_trends_snapshot()
        # 期待フィールドが無くても崩れないよう最低限を補完
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
    - デッキ順：ASSETS → AI BRIEF → RISK → MARKET → TODAY PLAN
    - 詳細リンクは今は持たせない（テンプレ側でも出さない）
    - ASSETS はリアルタイム集計
    - NEWS & TRENDS は 5分TTL（aiapp側でキャッシュ）
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

    # --- その他（今は器） ---
    ai_brief = _build_ai_brief(request.user)
    risk = _build_risk(request.user)
    market = _build_market()
    today_plan = _build_today_plan(request.user)

    # “デッキ”としてテンプレに渡す（順番固定）
    decks: List[Dict[str, Any]] = [
        {"key": "assets", "title": "ASSETS", "payload": assets},
        {"key": "ai_brief", "title": "AI BRIEF", "payload": ai_brief},
        {"key": "risk", "title": "RISK", "payload": risk},
        {"key": "market", "title": "MARKET", "payload": market},
        {"key": "today_plan", "title": "TODAY PLAN", "payload": today_plan},
        # NEWS & TRENDS は “別枠デッキ” として扱う（Homeで新鮮さ優先）
        {"key": "news_trends", "title": "NEWS & TRENDS", "payload": news_trends},
    ]

    context = {
        "today_label": _safe_localdate_str(),
        "decks": decks,
        # テンプレ側の制御用（今は詳細リンク無し）
        "enable_detail_links": False,
    }
    return render(request, "portfolio/home.html", context)