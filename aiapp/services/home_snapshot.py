# aiapp/services/home_snapshot.py
from __future__ import annotations

import logging
from typing import Any, Dict, List

from django.utils import timezone

from aiapp.models.home_deck_snapshot import HomeDeckSnapshot

logger = logging.getLogger(__name__)


def _safe_localdate():
    try:
        return timezone.localdate()
    except Exception:
        return timezone.now().date()


def build_home_decks_for_user(user) -> List[Dict[str, Any]]:
    """
    Homeで使う decks を生成（Home view と同じ並び）
    - ASSETS: portfolio/services/home_assets.py（既に realized.py と完全一致）
    - NEWS & TRENDS: aiapp/services/home_news_trends.py
    - TODAY PLAN: portfolio/views/home.py 側のロジックは “保存用にここへ移す” のが理想だが、
      まずは Home view の _build_today_plan_from_assets を import して共通化する。
    """
    # --- ASSETS ---
    try:
        from portfolio.services.home_assets import build_assets_snapshot
        assets = build_assets_snapshot(user)
        if not isinstance(assets, dict):
            assets = {"status": "error", "error": "assets snapshot is not dict"}
        assets.setdefault("status", "ok")
    except Exception as e:
        logger.exception("ASSETS build failed: %s", e)
        assets = {"status": "error", "error": str(e)}

    # --- NEWS & TRENDS ---
    try:
        from aiapp.services.home_news_trends import get_news_trends_snapshot  # type: ignore
        news_trends = get_news_trends_snapshot()
        if not isinstance(news_trends, dict):
            news_trends = {"status": "error", "error": "news snapshot is not dict", "items": []}
        news_trends.setdefault("status", "ok")
        news_trends.setdefault("items", [])
        news_trends.setdefault("sectors", [])
        news_trends.setdefault("trends", [])
        news_trends.setdefault("as_of", timezone.now().isoformat())
    except Exception as e:
        logger.exception("NEWS & TRENDS build failed: %s", e)
        news_trends = {
            "status": "stub",
            "as_of": timezone.now().isoformat(),
            "items": [],
            "sectors": [],
            "trends": [],
            "error": str(e),
        }

    # --- TODAY PLAN（Home view と同じ生成ロジックを使う） ---
    try:
        from portfolio.views.home import _build_today_plan_from_assets  # type: ignore
        today_plan = _build_today_plan_from_assets(assets, news_trends=news_trends)
        if not isinstance(today_plan, dict):
            today_plan = {"status": "error", "error": "today_plan is not dict", "tasks": []}
        today_plan.setdefault("status", "ok")
        today_plan.setdefault("as_of", timezone.now().isoformat())
    except Exception as e:
        logger.exception("TODAY PLAN build failed: %s", e)
        today_plan = {
            "status": "error",
            "as_of": timezone.now().isoformat(),
            "tasks": [],
            "error": str(e),
        }

    # --- STUB decks（今は要らない、でも順序は維持） ---
    def _stub(title: str) -> Dict[str, Any]:
        return {"title": title, "status": "stub", "as_of": timezone.now().isoformat()}

    decks: List[Dict[str, Any]] = [
        {"key": "assets", "title": "ASSETS", "payload": assets},
        {"key": "ai_brief", "title": "AI BRIEF", "payload": _stub("AI BRIEF")},
        {"key": "risk", "title": "RISK", "payload": _stub("RISK")},
        {"key": "market", "title": "MARKET", "payload": _stub("MARKET")},
        {"key": "today_plan", "title": "TODAY PLAN", "payload": today_plan},
        {"key": "news_trends", "title": "NEWS & TRENDS", "payload": news_trends},
    ]
    return decks


def upsert_today_snapshot(user) -> HomeDeckSnapshot:
    """
    今日の snapshot を upsert（毎朝 6:30 のcronで呼ぶ想定）
    """
    d = _safe_localdate()
    decks = build_home_decks_for_user(user)
    now = timezone.now()

    obj, _created = HomeDeckSnapshot.objects.update_or_create(
        user=user,
        snapshot_date=d,
        defaults={
            "decks": decks,
            "generated_at": now,
            "as_of": now.isoformat(),
        },
    )
    return obj