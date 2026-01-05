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


def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _as_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def _fmt_yen(v: float | int) -> str:
    try:
        n = int(round(float(v)))
    except Exception:
        n = 0
    return f"¥{n:,}"


def _get_goal_year_total_from_assets(assets: Dict[str, Any]) -> int:
    goals = (assets or {}).get("goals") or {}
    return _as_int(goals.get("year_total"), 0)


def _get_ytd_total_from_assets(assets: Dict[str, Any]) -> float:
    realized = (assets or {}).get("realized") or {}
    ytd = realized.get("ytd") or {}
    return _as_float(ytd.get("total"), 0.0)


def _pick_hot_sectors_text(news_trends: Dict[str, Any] | None, limit: int = 3) -> str:
    """
    news_trends["sectors"] = [{"sector": "...", "count": 12}, ...] を想定
    例: "今日の注目セクター: 半導体×6 / 自動車×4 / 銀行×3"
    """
    try:
        if not news_trends or not isinstance(news_trends, dict):
            return ""
        sectors = news_trends.get("sectors") or []
        if not isinstance(sectors, list) or len(sectors) == 0:
            return ""

        parts: List[str] = []
        for s in sectors[: max(0, int(limit))]:
            if not isinstance(s, dict):
                continue
            name = str(s.get("sector") or "").strip()
            cnt = s.get("count")
            if not name:
                continue
            if cnt is None:
                parts.append(name)
            else:
                try:
                    parts.append(f"{name}×{int(float(cnt))}")
                except Exception:
                    parts.append(name)

        if not parts:
            return ""
        return "今日の注目セクター: " + " / ".join(parts)
    except Exception:
        return ""


def _build_news_trends() -> Dict[str, Any]:
    """
    NEWS & TRENDS は “新鮮枠”：
    - スナップショットに固定せず、表示時に取得
    - home_news_trends 側の TTLキャッシュで「ほどほど」を担保
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
    assets: Dict[str, Any],
    news_trends: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    AI BRIEF = “今日の一言” + 要点
    - ASSETS(目標/ペース/進捗)を主軸に
    - NEWS/TRENDS 上位セクターを添える
    - macro_text があれば headline はそれを優先
    """
    now_iso = timezone.now().isoformat()
    try:
        pace = (assets or {}).get("pace") or {}
        total_m = (pace.get("total_need_per_month") or {})
        goal = _get_goal_year_total_from_assets(assets)
        ytd = _get_ytd_total_from_assets(assets)

        rem_m = _as_float(total_m.get("remaining"), 0.0)
        need_m = _as_float(total_m.get("need_per_slot"), 0.0)

        sector_hint = _pick_hot_sectors_text(news_trends, limit=3)
        macro_text = ""
        try:
            macro_text = str((news_trends or {}).get("macro_text") or "").strip()
        except Exception:
            macro_text = ""

        # headline（短く）
        if macro_text:
            headline = macro_text
        else:
            if goal <= 0:
                headline = "今日は“利益”より“再現性”を積む日。"
            else:
                if rem_m > 0:
                    headline = f"目標まで残り {_fmt_yen(rem_m)}。狙う型を絞ろう。"
                else:
                    headline = "目標ペースは達成圏。崩さない運用が勝ち。"

        bullets: List[str] = []
        if goal > 0:
            bullets.append(f"年目標 {_fmt_yen(goal)} / YTD {_fmt_yen(ytd)}")
            bullets.append(f"月ペース目安 {_fmt_yen(need_m)}（残り {_fmt_yen(rem_m)}）")
        else:
            bullets.append("目標が0（未設定）。まずは“型”を固定して運用。")
            bullets.append("損切り幅・利確R・回数上限を固定してブレを減らす。")

        if sector_hint:
            bullets.append(sector_hint)

        # NEWSから1つだけ“読む→条件化”の促進
        try:
            items = (news_trends or {}).get("items") or []
            if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
                t = str(items[0].get("title") or "").strip()
                src = str(items[0].get("source") or "").strip()
                if t:
                    bullets.append(f"見出し→条件化: {src}「{t}」を監視条件に変換。")
        except Exception:
            pass

        return {
            "title": "AI BRIEF",
            "status": "ok",
            "headline": headline,
            "bullets": bullets[:6],
            "as_of": now_iso,
        }
    except Exception as e:
        logger.exception("AI BRIEF build failed: %s", e)
        return {
            "title": "AI BRIEF",
            "status": "stub",
            "headline": "（準備中）",
            "bullets": [],
            "as_of": now_iso,
            "error": str(e),
        }


@login_required
def home(request):
    """
    Home = デッキ（横スワイプ）前提
    - デッキ順：ASSETS → AI BRIEF → NEWS & TRENDS
    - スナップショットは使わない（完全撤去）
    - ASSETS / NEWS は都度生成（NEWS側は service TTL でほどほど）
    """
    # --- ASSETS ---
    try:
        from ..services.home_assets import build_assets_snapshot

        assets = build_assets_snapshot(request.user)
        if not isinstance(assets, dict):
            assets = {"status": "error", "error": "assets snapshot is not dict"}
        assets.setdefault("status", "ok")
    except Exception as e:
        logger.exception("ASSETS build failed: %s", e)
        assets = {"status": "error", "error": str(e)}

    # --- NEWS & TRENDS ---
    news_trends = _build_news_trends()

    # --- AI BRIEF ---
    ai_brief = _build_ai_brief(assets, news_trends=news_trends)

    decks: List[Dict[str, Any]] = [
        {"key": "assets", "title": "ASSETS", "payload": assets},
        {"key": "ai_brief", "title": "AI BRIEF", "payload": ai_brief},
        {"key": "news_trends", "title": "NEWS & TRENDS", "payload": news_trends},
    ]

    context = {
        "today_label": _safe_localdate_str(),
        "decks": decks,
        "enable_detail_links": False,
    }
    return render(request, "home.html", context)