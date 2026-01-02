# aiapp/services/home_snapshot.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from django.utils import timezone
from django.db import transaction

logger = logging.getLogger(__name__)


def _safe_localdate():
    try:
        return timezone.localdate()
    except Exception:
        return timezone.now().date()


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


def _append_sector_hint(desc: str, sector_hint: str) -> str:
    if not sector_hint:
        return desc
    if not desc:
        return sector_hint
    return f"{desc}\n{sector_hint}"


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


def _build_today_plan_from_assets(
    assets: Dict[str, Any],
    news_trends: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    try:
        now_iso = timezone.now().isoformat()
        sector_hint = _pick_hot_sectors_text(news_trends, limit=3)

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

        if goal_year_total <= 0:
            tasks.append({
                "kind": "primary",
                "title": "目標が未設定：まず“型”を1つ固定",
                "desc": _append_sector_hint(
                    "年間目標が0なので、今日は利益額より“再現性（ルール順守）”を最優先。"
                    "損切り幅/利確R/回数上限などを1つ固定して運用。",
                    sector_hint,
                ),
            })
            tasks.append({
                "kind": "check",
                "title": "やることを減らす（ミス防止）",
                "desc": _append_sector_hint(
                    "新しいことを増やさず、同じ型だけで記録を厚くする。"
                    "逸脱しそうなら理由を1行メモ。",
                    sector_hint,
                ),
            })
            tasks.append({
                "kind": "check",
                "title": "ニュースは“条件化”だけする",
                "desc": _append_sector_hint(
                    "読むだけ禁止。気になった見出しを1つ選び、上抜け/下抜け/イベント日などの監視条件に変換しておく。",
                    sector_hint,
                ),
            })
            return {
                "title": "TODAY PLAN",
                "status": "ok",
                "as_of": now_iso,
                "tasks": tasks,
            }

        if rem_month_total > 0:
            tasks.append({
                "kind": "primary",
                "title": "必要ペースを意識して“やる形”を絞る",
                "desc": _append_sector_hint(
                    f"年目標 {goal_year_total:,} 円 / YTD {int(ytd_total):,} 円 → 残り {int(rem_month_total):,} 円。"
                    f"月 {int(need_m_total):,} 円 / 週 {int(need_w_total):,} 円ペースを目安に、狙う型を1つに絞る。",
                    sector_hint,
                ),
            })

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
                    "desc": _append_sector_hint(
                        f"残り {int(rem_b):,} 円 → 月 {int(need_b):,} 円ペース。"
                        "ここは“負け方を止める”が最優先（取り返し禁止/型固定）。",
                        sector_hint,
                    ),
                })

            tasks.append({
                "kind": "check",
                "title": "無理に増やさず、ルールで回す",
                "desc": _append_sector_hint(
                    "損切り/利確ルール優先。取り返しトレード禁止。迷ったらサイズ半分・回数上限で制御。",
                    sector_hint,
                ),
            })
        else:
            tasks.append({
                "kind": "ok",
                "title": "目標ペース上は問題なし（守り優先）",
                "desc": _append_sector_hint(
                    "無理に利益を積まず、崩さない運用（再現性・ポリシー順守）を優先。やらないことを増やす。",
                    sector_hint,
                ),
            })
            tasks.append({
                "kind": "check",
                "title": "エントリー数を減らす",
                "desc": _append_sector_hint(
                    "監視・仕込み・記録の精度を上げる。衝動エントリーは禁止。狙う時間帯/形を固定。",
                    sector_hint,
                ),
            })
            tasks.append({
                "kind": "check",
                "title": "勝ちパターンの“説明”を残す",
                "desc": _append_sector_hint(
                    "勝った取引より、ルール通りに“やらなかった”判断を1つ記録（これが後でAIの芯になる）。",
                    sector_hint,
                ),
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


def _build_ai_brief(
    assets: Dict[str, Any],
    news_trends: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    now_iso = timezone.now().isoformat()
    try:
        pace = (assets or {}).get("pace") or {}
        total_m = (pace.get("total_need_per_month") or {})
        goal = _get_goal_year_total_from_assets(assets)
        ytd = _get_ytd_total_from_assets(assets)

        rem_m = _as_float(total_m.get("remaining"), 0.0)
        need_m = _as_float(total_m.get("need_per_slot"), 0.0)

        sector_hint = _pick_hot_sectors_text(news_trends, limit=3)

        if goal <= 0:
            headline = "今日は“利益”より“再現性”を積む日。"
        else:
            if rem_m > 0:
                headline = f"目標まで残り {_fmt_yen(rem_m)}。狙う型を1つに絞ろう。"
            else:
                headline = "目標ペースは達成圏。崩さない運用が勝ち。"

        bullets: List[str] = []
        if goal > 0:
            bullets.append(f"年目標 {_fmt_yen(goal)} / YTD {_fmt_yen(ytd)}")
            bullets.append(f"月ペース 目安 {_fmt_yen(need_m)}（残り {_fmt_yen(rem_m)}）")
        else:
            bullets.append("目標が0（未設定）。まずは“型”を固定して運用。")
            bullets.append("損切り幅・利確R・回数上限を固定してブレを減らす。")

        if sector_hint:
            bullets.append(sector_hint)

        try:
            items = (news_trends or {}).get("items") or []
            if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
                t = str(items[0].get("title") or "").strip()
                src = str(items[0].get("source") or "").strip()
                if t:
                    bullets.append(f"見出し→条件化: {src}「{t}」を“監視条件”に変換。")
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


def _build_risk(user) -> Dict[str, Any]:
    now_iso = timezone.now().isoformat()
    try:
        from portfolio.models import UserSetting  # type: ignore

        setting, _ = UserSetting.objects.get_or_create(user=user)

        equity = _as_float(getattr(setting, "account_equity", 0), 0.0)
        risk_pct = _as_float(getattr(setting, "risk_pct", 0.0), 0.0)
        credit_usage_pct = _as_float(getattr(setting, "credit_usage_pct", 0.0), 0.0)

        risk_yen = int(round(equity * (risk_pct / 100.0))) if equity > 0 else 0

        metrics: List[Dict[str, Any]] = []

        metrics.append({
            "label": "口座残高（設定）",
            "value": _fmt_yen(equity),
            "kind": "ok" if equity >= 100_000 else "warn",
            "sub": "UserSetting.account_equity",
        })

        rp_kind = "ok"
        if risk_pct <= 0:
            rp_kind = "bad"
        elif risk_pct >= 3.0:
            rp_kind = "warn"

        metrics.append({
            "label": "1トレードのリスク％",
            "value": f"{risk_pct:.1f}%",
            "kind": rp_kind,
            "sub": "大きすぎると連敗で死ぬ",
        })

        ry_kind = "ok"
        if risk_yen <= 0:
            ry_kind = "bad"
        elif risk_yen >= 50_000:
            ry_kind = "warn"

        metrics.append({
            "label": "1トレードの最大損失（目安）",
            "value": _fmt_yen(risk_yen),
            "kind": ry_kind,
            "sub": "エントリー前に“損切り幅×枚数”で一致させる",
        })

        cu_kind = "ok"
        if credit_usage_pct <= 0:
            cu_kind = "warn"
        elif credit_usage_pct >= 90:
            cu_kind = "warn"

        metrics.append({
            "label": "信用余力の使用上限",
            "value": f"{credit_usage_pct:.0f}%",
            "kind": cu_kind,
            "sub": "突っ込みすぎ防止",
        })

        def _pair(name: str, lev: float, hc: float) -> str:
            return f"{name}: 倍率 {lev:.2f} / HC {int(round(hc*100)):d}%"

        notes: List[str] = []
        try:
            lr = _as_float(getattr(setting, "leverage_rakuten", 0.0), 0.0)
            hr = _as_float(getattr(setting, "haircut_rakuten", 0.0), 0.0)
            lm = _as_float(getattr(setting, "leverage_matsui", 0.0), 0.0)
            hm = _as_float(getattr(setting, "haircut_matsui", 0.0), 0.0)
            ls = _as_float(getattr(setting, "leverage_sbi", 0.0), 0.0)
            hs = _as_float(getattr(setting, "haircut_sbi", 0.0), 0.0)

            if lr > 0:
                notes.append(_pair("楽天", lr, hr))
            if ls > 0:
                notes.append(_pair("SBI", ls, hs))
            if lm > 0:
                notes.append(_pair("松井", lm, hm))
        except Exception:
            pass

        alerts: List[Dict[str, Any]] = []

        if risk_pct <= 0:
            alerts.append({
                "kind": "bad",
                "title": "リスク％が0（または未設定）",
                "desc": "まず 0.5〜1.5% くらいに設定して、“1回の損失上限”を固定しよう。",
            })

        if equity <= 0:
            alerts.append({
                "kind": "bad",
                "title": "口座残高が0（または未設定）",
                "desc": "数量計算が破綻するので、口座残高（設定）を入れてね。",
            })

        rules = [
            "迷ったらサイズ半分（0.5R）",
            "取り返しトレード禁止（連敗時は回数上限）",
            "損切りを先に置けないなら入らない",
        ]

        return {
            "title": "RISK",
            "status": "ok",
            "metrics": metrics,
            "alerts": alerts,
            "rules": rules,
            "notes": notes,
            "as_of": now_iso,
        }

    except Exception as e:
        logger.exception("RISK build failed: %s", e)
        return {
            "title": "RISK",
            "status": "stub",
            "metrics": [],
            "alerts": [],
            "rules": [],
            "notes": [],
            "as_of": now_iso,
            "error": str(e),
        }


def _build_market(news_trends: Dict[str, Any] | None = None) -> Dict[str, Any]:
    now_iso = timezone.now().isoformat()
    try:
        hot = _pick_hot_sectors_text(news_trends, limit=4)

        news_items: List[Dict[str, Any]] = []
        items = (news_trends or {}).get("items") or []
        if isinstance(items, list):
            for it in items[:6]:
                if not isinstance(it, dict):
                    continue
                title = str(it.get("title") or "").strip()
                link = str(it.get("link") or "").strip()
                src = str(it.get("source") or "").strip()
                host = str(it.get("host") or "").strip()
                if not title:
                    continue
                news_items.append({
                    "title": title,
                    "link": link,
                    "source": src,
                    "host": host,
                })

        trend_items: List[Dict[str, Any]] = []
        trends = (news_trends or {}).get("trends") or []
        if isinstance(trends, list):
            for it in trends[:6]:
                if not isinstance(it, dict):
                    continue
                title = str(it.get("title") or "").strip()
                link = str(it.get("link") or "").strip()
                host = str(it.get("host") or "").strip()
                if not title:
                    continue
                trend_items.append({
                    "title": title,
                    "link": link,
                    "host": host,
                })

        summary = "（準備中）"
        if hot:
            summary = hot
        elif news_items:
            summary = "ニュース上位から相場の“主語”を掴む"
        elif trend_items:
            summary = "ネット/トレンド上位から“テーマ”を掴む"

        return {
            "title": "MARKET",
            "status": "ok",
            "summary": summary,
            "news": news_items,
            "trends": trend_items,
            "as_of": now_iso,
        }
    except Exception as e:
        logger.exception("MARKET build failed: %s", e)
        return {
            "title": "MARKET",
            "status": "stub",
            "summary": "（準備中）",
            "news": [],
            "trends": [],
            "as_of": now_iso,
            "error": str(e),
        }


def _validate_decks_shape(decks: Any) -> Tuple[bool, str]:
    if not isinstance(decks, list) or len(decks) == 0:
        return False, "decks is not list or empty"
    for x in decks:
        if not isinstance(x, dict):
            return False, "decks contains non-dict item"
        if "key" not in x or "title" not in x or "payload" not in x:
            return False, "decks item missing key/title/payload"
    return True, ""


@transaction.atomic
def upsert_today_snapshot(user) -> None:
    """
    今日分の HomeDeckSnapshot を生成して保存（上書き）
    """
    from aiapp.models.home_deck_snapshot import HomeDeckSnapshot  # type: ignore

    d = _safe_localdate()
    now_iso = timezone.now().isoformat()

    # --- ASSETS（リアルタイム） ---
    try:
        from portfolio.services.home_assets import build_assets_snapshot  # type: ignore
        assets = build_assets_snapshot(user)
        if not isinstance(assets, dict):
            assets = {"status": "error", "error": "assets snapshot is not dict"}
        assets.setdefault("status", "ok")
    except Exception as e:
        logger.exception("ASSETS build failed (snapshot): %s", e)
        assets = {"status": "error", "error": str(e)}

    # --- NEWS & TRENDS（取得） ---
    news_trends = _build_news_trends()

    # --- AI BRIEF / RISK / MARKET / TODAY PLAN ---
    ai_brief = _build_ai_brief(assets, news_trends=news_trends)
    risk = _build_risk(user)
    market = _build_market(news_trends=news_trends)
    today_plan = _build_today_plan_from_assets(assets, news_trends=news_trends)

    decks: List[Dict[str, Any]] = [
        {"key": "assets", "title": "ASSETS", "payload": assets},
        {"key": "ai_brief", "title": "AI BRIEF", "payload": ai_brief},
        {"key": "risk", "title": "RISK", "payload": risk},
        {"key": "market", "title": "MARKET", "payload": market},
        {"key": "today_plan", "title": "TODAY PLAN", "payload": today_plan},
        {"key": "news_trends", "title": "NEWS & TRENDS", "payload": news_trends},
    ]

    ok, msg = _validate_decks_shape(decks)
    if not ok:
        raise ValueError(f"snapshot decks invalid: {msg}")

    HomeDeckSnapshot.objects.update_or_create(
        user=user,
        snapshot_date=d,
        defaults={
            "decks": decks,
            "generated_at": timezone.now(),
            "as_of": now_iso,
        },
    )