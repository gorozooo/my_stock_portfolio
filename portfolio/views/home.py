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


def _build_ai_brief(user) -> Dict[str, Any]:
    return {
        "title": "AI BRIEF",
        "status": "stub",
        "headline": "（準備中）",
        "bullets": [],
        "as_of": timezone.now().isoformat(),
    }


def _build_risk(user) -> Dict[str, Any]:
    return {
        "title": "RISK",
        "status": "stub",
        "metrics": [],
        "alerts": [],
        "as_of": timezone.now().isoformat(),
    }


def _build_market() -> Dict[str, Any]:
    return {
        "title": "MARKET",
        "status": "stub",
        "summary": "（準備中）",
        "items": [],
        "as_of": timezone.now().isoformat(),
    }


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


def _get_goal_year_total_from_assets(assets: Dict[str, Any]) -> int:
    goals = (assets or {}).get("goals") or {}
    return _as_int(goals.get("year_total"), 0)


def _get_ytd_total_from_assets(assets: Dict[str, Any]) -> float:
    realized = (assets or {}).get("realized") or {}
    ytd = realized.get("ytd") or {}
    return _as_float(ytd.get("total"), 0.0)


def _build_today_plan_from_assets(assets: Dict[str, Any]) -> Dict[str, Any]:
    """
    TODAY PLAN を assets(pace + goal + ytd) から自動生成
    - t.tasks を home.html が描画する前提
    - warn（黄色）判定は home.html / home.css の思想に合わせて：
        ・残り（remaining）>0 ＝未達
        ・必要ペース（need_per_slot）>0 ＝未達
      に統一する（表示側は warn/pos）
    """
    try:
        now_iso = timezone.now().isoformat()

        pace = (assets or {}).get("pace") or {}
        total_m = (pace.get("total_need_per_month") or {})
        total_w = (pace.get("total_need_per_week") or {})
        by = pace.get("by_broker_rows") or []
        if not isinstance(by, list):
            by = []

        goal_year_total = _get_goal_year_total_from_assets(assets)
        ytd_total = _get_ytd_total_from_assets(assets)

        rem_month_total = _as_float(total_m.get("remaining"), 0.0)
        need_m_total = _as_float(total_m.get("need_per_slot"), 0.0)
        need_w_total = _as_float(total_w.get("need_per_slot"), 0.0)

        tasks: List[Dict[str, Any]] = []

        # ========== CASE 1: 年間目標が未設定 ==========
        if goal_year_total <= 0:
            tasks.append({
                "kind": "primary",
                "title": "目標が未設定：まず“型”を1つ固定",
                "desc": "年間目標が0なので、今日は利益額より“再現性（ルール順守）”を最優先。損切り幅/利確R/回数上限などを1つ固定して運用。",
            })
            tasks.append({
                "kind": "check",
                "title": "やることを減らす（ミス防止）",
                "desc": "新しいことを増やさず、同じ型だけで記録を厚くする。逸脱しそうなら理由を1行メモ。",
            })
            tasks.append({
                "kind": "check",
                "title": "ニュースは“条件化”だけする",
                "desc": "読むだけ禁止。気になった見出しを1つ選び、上抜け/下抜け/イベント日などの監視条件に変換しておく。",
            })

            return {
                "title": "TODAY PLAN",
                "status": "ok",
                "as_of": now_iso,
                "tasks": tasks,
            }

        # ========== CASE 2: 目標あり → 進捗で分岐 ==========
        # ここでの rem_month_total は「年目標 - ytd」から算出された“残り”を元に
        # home_assets.py が作っている想定（あなたの前提）
        if rem_month_total > 0:
            # 未達（追い込み）
            tasks.append({
                "kind": "primary",
                "title": "必要ペースを意識して“やる形”を絞る",
                "desc": f"年目標 {goal_year_total:,} 円 / YTD {int(ytd_total):,} 円 → 残り {int(rem_month_total):,} 円。月 {int(need_m_total):,} 円 / 週 {int(need_w_total):,} 円ペースを目安に、狙う型を1つに絞る。",
            })

            # broker別：need_per_slot が “大きい順” 上位2つ（未達の主因）
            def _need_per_slot_of_row(r: Dict[str, Any]) -> float:
                pm = (r.get("pace_month") or {})
                return _as_float(pm.get("need_per_slot"), 0.0)

            by_sorted = sorted(by, key=_need_per_slot_of_row, reverse=True)
            top = by_sorted[:2]

            for r in top:
                pm = r.get("pace_month") or {}
                need_b = _as_float(pm.get("need_per_slot"), 0.0)
                rem_b = _as_float(pm.get("remaining"), 0.0)
                label = (r.get("label") or "").strip() or "（不明）"
                tasks.append({
                    "kind": "broker",
                    "title": f"{label} を優先",
                    "desc": f"残り {int(rem_b):,} 円 → 月 {int(need_b):,} 円ペース。ここは“負け方を止める”が最優先（取り返し禁止/型固定）。",
                })

            tasks.append({
                "kind": "check",
                "title": "無理に増やさず、ルールで回す",
                "desc": "損切り/利確ルール優先。取り返しトレード禁止。迷ったらサイズ半分・回数上限で制御。",
            })

        else:
            # 達成圏（守り）
            tasks.append({
                "kind": "ok",
                "title": "目標ペース上は問題なし（守り優先）",
                "desc": "無理に利益を積まず、崩さない運用（再現性・ポリシー順守）を優先。やらないことを増やす。",
            })
            tasks.append({
                "kind": "check",
                "title": "エントリー数を減らす",
                "desc": "監視・仕込み・記録の精度を上げる。衝動エントリーは禁止。狙う時間帯/形を固定。",
            })
            tasks.append({
                "kind": "check",
                "title": "勝ちパターンの“説明”を残す",
                "desc": "勝った取引より、ルール通りに“やらなかった”判断を1つ記録（これが後でAIの芯になる）。",
            })

        return {
            "title": "TODAY PLAN",
            "status": "ok",
            "as_of": now_iso,
            "tasks": tasks,
        }

    except Exception as e:
        logger.exception("TODAY PLAN build failed: %s", e)
        return {
            "title": "TODAY PLAN",
            "status": "error",
            "as_of": timezone.now().isoformat(),
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