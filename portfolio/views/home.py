# portfolio/views/home.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

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


def _build_news_trends() -> Dict[str, Any]:
    """
    NEWS & TRENDS は “新鮮枠”：
    - 表示時に取得（スナップショットしない）
    - home_news_trends 側のTTLキャッシュで「ほどほど」を担保
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

        # as_of は「取得した今」を優先
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


def _build_ai_brief_from_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    A段階：まずは “素材に沿った発言” にする（テンプレを減らす土台）
    - summary / reasons / concerns / escape の枠は「見やすさ」と「UI安定」のために残す
      ※今後、UI側で “折りたたみ/長文” に対応すれば制約は緩められる
    """
    now_iso = timezone.now().isoformat()

    try:
        user_state = (ctx or {}).get("user_state") or {}
        port = (ctx or {}).get("portfolio_state") or {}
        beh = (ctx or {}).get("behavior_state") or {}
        mk = (ctx or {}).get("market_state") or {}
        cons = (ctx or {}).get("constraints") or {}

        goal = _as_int(port.get("goal_year_total"), 0)
        ytd = _as_float(port.get("realized_ytd"), 0.0)
        mtd = _as_float(port.get("realized_mtd"), 0.0)

        # “主語” を作る：themesが強い順に
        themes = mk.get("themes") or []
        theme_top = ""
        if isinstance(themes, list) and themes:
            t0 = themes[0]
            if isinstance(t0, dict):
                theme_top = str(t0.get("name") or "").strip()

        # news_top 1件目（読ませるための“外部の一言”）
        news_top = mk.get("news_top") or []
        first_news_title = ""
        first_news_source = ""
        if isinstance(news_top, list) and news_top:
            n0 = news_top[0]
            if isinstance(n0, dict):
                first_news_title = str(n0.get("title") or "").strip()
                first_news_source = str(n0.get("source") or "").strip()

        # 行動（直近7日）
        last7 = (beh.get("last_7d") or {}) if isinstance(beh, dict) else {}
        trades7 = _as_int(last7.get("trades"), 0)
        pnl7 = _as_float(last7.get("pnl_sum"), 0.0)

        # リスク設定
        equity = _as_int(user_state.get("equity"), 0)
        risk_pct = _as_float(user_state.get("risk_pct"), 0.0)
        risk_yen = user_state.get("risk_yen", None)

        # summary（固定文を避けて、素材の組み合わせで組み立てる）
        # ここは “短いが毎日変わる” を狙う
        parts: List[str] = []

        if theme_top:
            parts.append(f"主語は「{theme_top}」。")

        if goal > 0:
            parts.append(f"年目標 {_fmt_yen(goal)} に対して、いまYTD {_fmt_yen(ytd)}。")
        else:
            parts.append("年目標は未設定。今日は結果より“型”を優先。")

        if trades7 > 0:
            sign = "＋" if pnl7 >= 0 else "−"
            parts.append(f"直近7日 {trades7}件、合計 {sign}{_fmt_yen(abs(pnl7))}。")
        else:
            parts.append("直近7日は記録が薄い。まずはログを厚くする日。")

        # 迷い要素（外部ニュースが強い＝ノイズが増える）を入れる
        if first_news_title:
            parts.append("外部ノイズは増えてる。読むより条件化。")

        summary = " ".join([p for p in parts if p]).strip()
        if not summary:
            summary = "今日は“状況の棚卸し”から。数字→制約→焦点の順に整える。"

        # reasons（最大5）：内部データ中心＋外部は1つだけ
        reasons: List[str] = []
        reasons.append(f"YTD {_fmt_yen(ytd)} / MTD {_fmt_yen(mtd)}（実現損益）")
        if goal > 0:
            reasons.append(f"年目標 {_fmt_yen(goal)}（設定値）")
        if equity > 0 and risk_pct > 0:
            if risk_yen is not None:
                reasons.append(f"1回の損失上限 目安 {_fmt_yen(risk_yen)}（{risk_pct:.1f}%）")
            else:
                reasons.append(f"リスク設定 {risk_pct:.1f}%（口座残高未設定だと数量計算が弱い）")
        if trades7 > 0:
            reasons.append(f"直近7日：{trades7}件 / 合計損益 {_fmt_yen(pnl7)}")
        if first_news_title:
            reasons.append(f"外部：{first_news_source}「{first_news_title}」→ 監視条件に変換")

        reasons = [x for x in reasons if x][:5]

        # concerns（最大2）：本当に危ないものを優先
        concerns: List[str] = []
        if goal <= 0:
            concerns.append("目標が未設定だと、日々の判断軸がブレやすい。")
        if equity <= 0:
            concerns.append("口座残高（設定）が0だと、リスク額が固定できない。")
        if not concerns and first_news_title:
            concerns.append("外部ニュースの量が多い日は、判断が散って手数が増えやすい。")
        concerns = concerns[:2]

        # escape（1行）：hard_rules から1つ採用
        hard_rules = cons.get("hard_rules") or []
        escape = ""
        if isinstance(hard_rules, list) and hard_rules:
            escape = str(hard_rules[0] or "").strip()
        if not escape:
            escape = "迷ったら入らない。まず監視条件だけ作って待つ。"

        return {
            "title": "AI BRIEF",
            "status": "ok",
            "as_of": now_iso,
            "summary": summary,
            "reasons": reasons,
            "concerns": concerns,
            "escape": escape,
        }

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


@login_required
def home(request):
    """
    Home = デッキ（横スワイプ）前提
    - スナップショットは使わない（毎回生成）
    - デッキ：ASSETS → AI BRIEF → NEWS & TRENDS
    """
    # --- ASSETS（毎回生成：内部データ）---
    try:
        from ..services.home_assets import build_assets_snapshot

        assets = build_assets_snapshot(request.user)
        if not isinstance(assets, dict):
            assets = {"status": "error", "error": "assets snapshot is not dict"}
        assets.setdefault("status", "ok")
    except Exception as e:
        logger.exception("ASSETS build failed: %s", e)
        assets = {"status": "error", "error": str(e)}

    # --- NEWS & TRENDS（外部：ほどほどTTLは service側）---
    news_trends = _build_news_trends()

    # --- AI BRIEF：素材(ctx)を作ってログに出す（A段階）---
    ctx: Dict[str, Any] = {}
    try:
        from aiapp.services.brief_context import build_brief_context, log_brief_context  # type: ignore

        ctx = build_brief_context(user=request.user, assets=assets, news_trends=news_trends)
        log_brief_context(ctx)
    except Exception as e:
        logger.exception("AI BRIEF context build failed: %s", e)
        ctx = {"error": str(e)}

    ai_brief = _build_ai_brief_from_context(ctx if isinstance(ctx, dict) else {})

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