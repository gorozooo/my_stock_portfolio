# portfolio/views/home.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

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


def _safe_localdate():
    try:
        return timezone.localdate()
    except Exception:
        return timezone.now().date()


def _build_news_trends() -> Dict[str, Any]:
    """
    NEWS & TRENDS は “新鮮枠”：
    - スナップショットに固定せず、表示時に取得
    - home_news_trends 側の 5分TTLキャッシュで「ほどほど」を担保
    """
    now_iso = timezone.now().isoformat()
    try:
        from aiapp.services.home_news_trends import get_news_trends_snapshot  # type: ignore

        snap = get_news_trends_snapshot()
        if not isinstance(snap, dict):
            return {"status": "error", "error": "news snapshot is not dict", "items": [], "as_of": now_iso}

        snap.setdefault("status", "ok")
        snap.setdefault("items", [])
        snap.setdefault("sectors", [])
        snap.setdefault("trends", [])
        snap.setdefault("macro_text", "")
        # as_of は「取得した今」を優先（固定化を避ける）
        snap["as_of"] = now_iso
        return snap
    except Exception as e:
        logger.exception("NEWS & TRENDS build failed: %s", e)
        return {
            "status": "stub",
            "as_of": now_iso,
            "items": [],
            "sectors": [],
            "trends": [],
            "macro_text": "",
            "error": str(e),
        }


def _build_ai_brief(
    *,
    user,
    assets: Dict[str, Any],
    news_trends: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    AI BRIEF（人格核）
    - Context（内部データ + 外部ニュース）を1枚に集約
    - “思考→発言”エンジンで生成（テンプレ選択じゃなく判断から作る）
    - Contextはログに出す（ai_brief_ctx.log）
    """
    now_iso = timezone.now().isoformat()
    try:
        from aiapp.services.brief_context import build_brief_context, log_brief_context  # type: ignore
        from aiapp.services.ai_brief_engine import build_ai_brief_from_ctx  # type: ignore

        ctx = build_brief_context(user=user, assets=assets, news_trends=news_trends)
        log_brief_context(ctx)

        brief = build_ai_brief_from_ctx(ctx=ctx, user_id=int(getattr(user, "id", 0) or 0))
        if not isinstance(brief, dict):
            return {"title": "AI BRIEF", "status": "error", "as_of": now_iso, "summary": "（生成失敗）", "reasons": [], "concerns": [], "escape": ""}

        brief.setdefault("title", "AI BRIEF")
        brief.setdefault("status", "ok")
        brief.setdefault("as_of", now_iso)
        return brief

    except Exception as e:
        logger.exception("AI BRIEF build failed: %s", e)
        return {
            "title": "AI BRIEF",
            "status": "stub",
            "as_of": now_iso,
            "summary": "（準備中）",
            "reasons": [],
            "concerns": [],
            "escape": "",
            "error": str(e),
        }


def _load_today_home_snapshot(user) -> Tuple[List[Dict[str, Any]] | None, str | None]:
    """
    6:30 生成の HomeDeckSnapshot を優先して読み込む。
    """
    try:
        from aiapp.models.home_deck_snapshot import HomeDeckSnapshot  # type: ignore

        d = _safe_localdate()
        snap = HomeDeckSnapshot.objects.filter(user=user, snapshot_date=d).first()
        if not snap:
            return None, None

        decks = snap.decks
        if not isinstance(decks, list) or len(decks) == 0:
            return None, "snapshot decks is empty"

        for x in decks:
            if not isinstance(x, dict):
                return None, "snapshot decks contains non-dict item"
            if "key" not in x or "title" not in x or "payload" not in x:
                return None, "snapshot decks item missing key/title/payload"

        return decks, None
    except Exception as e:
        logger.exception("Home snapshot load failed: %s", e)
        return None, str(e)


def _override_deck_payload(
    decks: List[Dict[str, Any]],
    key: str,
    payload: Dict[str, Any],
    title_fallback: str,
) -> List[Dict[str, Any]]:
    """
    decks 内の key の payload を差し替える（無ければ末尾追加）
    """
    out: List[Dict[str, Any]] = []
    replaced = False
    for d in decks:
        if isinstance(d, dict) and d.get("key") == key:
            out.append(
                {
                    "key": key,
                    "title": str(d.get("title") or title_fallback),
                    "payload": payload,
                }
            )
            replaced = True
        else:
            out.append(d)
    if not replaced:
        out.append({"key": key, "title": title_fallback, "payload": payload})
    return out


@login_required
def home(request):
    """
    Home = デッキ（横スワイプ）前提
    - デッキ順：ASSETS → AI BRIEF → NEWS & TRENDS
    - snapshot があればそれを優先（ただし ASSETS と NEWS は常に新鮮）
    """
    # ===== snapshot 優先 =====
    snap_decks, snap_err = _load_today_home_snapshot(request.user)
    if snap_decks:
        # --- ASSETS（常に新鮮） ---
        try:
            from ..services.home_assets import build_assets_snapshot

            assets = build_assets_snapshot(request.user)
            if not isinstance(assets, dict):
                assets = {"status": "error", "error": "assets snapshot is not dict"}
            assets.setdefault("status", "ok")
        except Exception as e:
            logger.exception("ASSETS build failed: %s", e)
            assets = {"status": "error", "error": str(e)}

        # --- NEWS & TRENDS（常に新鮮） ---
        news_trends = _build_news_trends()

        # --- AI BRIEF（人格核） ---
        ai_brief = _build_ai_brief(user=request.user, assets=assets, news_trends=news_trends)

        # snapshot decks の該当payloadだけ差し替え
        decks = list(snap_decks)
        decks = _override_deck_payload(decks, "assets", assets, "ASSETS")
        decks = _override_deck_payload(decks, "ai_brief", ai_brief, "AI BRIEF")
        decks = _override_deck_payload(decks, "news_trends", news_trends, "NEWS & TRENDS")

        context = {
            "today_label": _safe_localdate_str(),
            "decks": decks,
            "enable_detail_links": False,
            "home_snapshot_used": True,
            "home_snapshot_error": None,
        }
        return render(request, "home.html", context)

    # ===== フォールバック（その場生成） =====
    try:
        from ..services.home_assets import build_assets_snapshot

        assets = build_assets_snapshot(request.user)
        if not isinstance(assets, dict):
            assets = {"status": "error", "error": "assets snapshot is not dict"}
        assets.setdefault("status", "ok")
    except Exception as e:
        logger.exception("ASSETS build failed: %s", e)
        assets = {"status": "error", "error": str(e)}

    news_trends = _build_news_trends()
    ai_brief = _build_ai_brief(user=request.user, assets=assets, news_trends=news_trends)

    decks: List[Dict[str, Any]] = [
        {"key": "assets", "title": "ASSETS", "payload": assets},
        {"key": "ai_brief", "title": "AI BRIEF", "payload": ai_brief},
        {"key": "news_trends", "title": "NEWS & TRENDS", "payload": news_trends},
    ]

    context = {
        "today_label": _safe_localdate_str(),
        "decks": decks,
        "enable_detail_links": False,
        "home_snapshot_used": False,
        "home_snapshot_error": snap_err,
    }
    return render(request, "home.html", context)