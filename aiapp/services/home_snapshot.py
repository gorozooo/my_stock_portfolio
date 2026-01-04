# aiapp/services/home_snapshot.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# =========================
# basics
# =========================
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


def _clamp_text(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    return s[: max(0, max_len - 1)] + "…"


def _as_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


# =========================
# assets helpers
# =========================
def _get_goal_year_total_from_assets(assets: Dict[str, Any]) -> int:
    goals = (assets or {}).get("goals") or {}
    return _as_int(goals.get("year_total"), 0)


def _get_ytd_total_from_assets(assets: Dict[str, Any]) -> float:
    realized = (assets or {}).get("realized") or {}
    ytd = realized.get("ytd") or {}
    return _as_float(ytd.get("total"), 0.0)


def _pick_hot_sectors_text(news_trends: Dict[str, Any] | None, limit: int = 3) -> str:
    """
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


def _append_sector_hint(desc: str, sector_hint: str) -> str:
    if not sector_hint:
        return desc
    if not desc:
        return sector_hint
    return f"{desc}\n{sector_hint}"


# =========================
# snapshot fallback for news_trends
# =========================
def _find_deck_payload(decks: Any, key: str) -> Optional[Dict[str, Any]]:
    if not isinstance(decks, list):
        return None
    for d in decks:
        try:
            if not isinstance(d, dict):
                continue
            if d.get("key") != key:
                continue
            p = d.get("payload")
            return p if isinstance(p, dict) else None
        except Exception:
            continue
    return None


def _looks_news_trends_valid(nt: Any) -> bool:
    """
    NEWS取得失敗でも “直前の保存” を使えるようにするための軽い妥当性チェック。
    - items / trends / sectors のどれかが入っている or macro_text がある → valid
    """
    if not isinstance(nt, dict):
        return False

    items = nt.get("items")
    trends = nt.get("trends")
    sectors = nt.get("sectors")
    macro_text = nt.get("macro_text")

    has_items = isinstance(items, list) and len(items) > 0
    has_trends = isinstance(trends, list) and len(trends) > 0
    has_sectors = isinstance(sectors, list) and len(sectors) > 0
    has_macro = isinstance(macro_text, str) and macro_text.strip() != ""

    return bool(has_items or has_trends or has_sectors or has_macro)


def _load_latest_snapshot_news_trends(user) -> Optional[Dict[str, Any]]:
    """
    直前の HomeDeckSnapshot から news_trends payload を取り出す。
    """
    try:
        from aiapp.models.home_deck_snapshot import HomeDeckSnapshot  # type: ignore

        snap = (
            HomeDeckSnapshot.objects
            .filter(user=user)
            .order_by("-snapshot_date", "-generated_at")
            .first()
        )
        if not snap:
            return None

        prev_nt = _find_deck_payload(snap.decks, "news_trends")
        if _looks_news_trends_valid(prev_nt):
            return prev_nt
        return None
    except Exception:
        return None


def _build_news_trends(force_refresh: bool = False, user=None) -> Dict[str, Any]:
    """
    - 6:30生成のスナップショットでは “force_refresh=True” 推奨（その朝の固定を作るため）
    - 必須キー macro_text を必ず持たせる（A/B対応）
    - 失敗 or 空っぽなら、直前スナップショットの news_trends で補完（障害時フォールバック）
    """
    now_iso = timezone.now().isoformat()

    snap: Dict[str, Any]
    try:
        from aiapp.services.home_news_trends import get_news_trends_snapshot  # type: ignore

        snap = get_news_trends_snapshot(force_refresh=force_refresh)
        if not isinstance(snap, dict):
            snap = {"status": "error", "error": "news snapshot is not dict", "items": []}

        snap.setdefault("status", "ok")
        snap.setdefault("items", [])
        snap.setdefault("sectors", [])
        snap.setdefault("trends", [])
        snap.setdefault("macro_text", "")  # A/B: 必須
        snap.setdefault("as_of", now_iso)
    except Exception as e:
        logger.exception("NEWS & TRENDS build failed: %s", e)
        snap = {
            "status": "stub",
            "as_of": now_iso,
            "items": [],
            "sectors": [],
            "trends": [],
            "macro_text": "",
            "error": str(e),
        }

    # フォールバック（取得失敗/空っぽ → 直前の保存）
    if not _looks_news_trends_valid(snap):
        if user is not None:
            prev_nt = _load_latest_snapshot_news_trends(user)
            if prev_nt is not None:
                used = dict(prev_nt)  # shallow copy
                used.setdefault("items", [])
                used.setdefault("sectors", [])
                used.setdefault("trends", [])
                used.setdefault("macro_text", "")
                used["status"] = "fallback"
                used["as_of"] = now_iso
                snap = used

    return snap


# =========================
# Deck2: AI BRIEF (人格)
# =========================
def _build_ai_brief(
    assets: Dict[str, Any],
    news_trends: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    最終仕様へ寄せる（ただしテンプレ互換のため headline/bullets も残す）

    - 要約（100〜160文字）
    - 根拠（最大5）: アプリ内データのみ
    - 懸念（最大2）: アプリ内データのみ
    - 逃げ道（1行）: 行動の保険
    - 外部ニュースは “参考” として最大1件のみ（根拠に混ぜない）
    """
    now_iso = timezone.now().isoformat()

    try:
        pace = _as_dict((assets or {}).get("pace"))
        total_m = _as_dict(pace.get("total_need_per_month"))
        total_w = _as_dict(pace.get("total_need_per_week"))

        goal = _get_goal_year_total_from_assets(assets)
        ytd = _get_ytd_total_from_assets(assets)

        rem_m = _as_float(total_m.get("remaining"), 0.0)
        need_m = _as_float(total_m.get("need_per_slot"), 0.0)
        need_w = _as_float(total_w.get("need_per_slot"), 0.0)

        # --- 要約（人格の一言） ---
        # ※ここは “アプリ内データのみ” で生成する（外部ニュース禁止）
        if goal <= 0:
            summary = "今日は利益より再現性を積む日。損切り幅・利確R・回数上限を固定して、同じ型だけで記録を厚くする。"
        else:
            if rem_m > 0:
                summary = (
                    f"年目標{_fmt_yen(goal)}に対してYTD{_fmt_yen(ytd)}。"
                    f"今月の残りは{_fmt_yen(rem_m)}なので、狙う型を1つに絞って“負け方を止める”を優先。"
                )
            else:
                summary = (
                    f"目標ペースは達成圏（年目標{_fmt_yen(goal)} / YTD{_fmt_yen(ytd)}）。"
                    "今日は増やすより崩さない。衝動エントリーを減らしてルール順守を優先。"
                )

        summary = _clamp_text(summary, 160)

        # --- 根拠（最大5）: アプリ内データのみ ---
        reasons: List[str] = []
        if goal > 0:
            reasons.append(f"年目標: {_fmt_yen(goal)} / YTD: {_fmt_yen(ytd)}")
            reasons.append(f"月ペース目安: {_fmt_yen(need_m)}（残り: {_fmt_yen(rem_m)}）")
            if need_w != 0:
                reasons.append(f"週ペース目安: {_fmt_yen(need_w)}")
        else:
            reasons.append("年目標が未設定（0）。利益額よりルール順守を優先すべき状態。")

        # broker別の“詰まってる所”を2つだけ（アプリ内データ）
        by = _as_list(pace.get("by_broker_rows"))
        def _need_per_slot_of_row(r: Dict[str, Any]) -> float:
            pm = _as_dict(r.get("pace_month"))
            return _as_float(pm.get("need_per_slot"), 0.0)

        try:
            by_sorted = sorted([_as_dict(x) for x in by], key=_need_per_slot_of_row, reverse=True)
            for r in by_sorted[:2]:
                label = (r.get("label") or "").strip() or "（不明）"
                pm = _as_dict(r.get("pace_month"))
                need_b = _as_float(pm.get("need_per_slot"), 0.0)
                rem_b = _as_float(pm.get("remaining"), 0.0)
                reasons.append(f"{label}: 月ペース{_fmt_yen(need_b)} / 残り{_fmt_yen(rem_b)}")
        except Exception:
            pass

        reasons = reasons[:5]

        # --- 懸念（最大2）: アプリ内データのみ ---
        concerns: List[str] = []
        # 未達が大きい時は“取り返し”が最大リスク
        if goal > 0 and rem_m > 0:
            concerns.append("未達の焦りで取り返しトレードが起きやすい。エントリー数とサイズを先に制限する。")

        # risk設定が未設定/過大なら懸念に出す（アプリ内）
        try:
            from portfolio.models import UserSetting  # type: ignore
            # userはここに無いので、RISK側で詳しく出す。AI BRIEFは一般化して1行に留める。
            # （ここでは“設定が入ってる前提”でやりすぎない）
        except Exception:
            pass

        # 何も無いと寂しいので1つだけ保険
        if not concerns:
            concerns.append("迷いが出たら“やらない”を優先。型が揃うまで新しい動きは増やさない。")

        concerns = concerns[:2]

        # --- 逃げ道（1行） ---
        escape = "迷ったらサイズ半分・回数上限。損切りを先に置けないなら入らない。"

        # --- 外部ニュースは“参考”として最大1件のみ ---
        ref_news: Optional[Dict[str, Any]] = None
        try:
            items = _as_list((news_trends or {}).get("items"))
            if items and isinstance(items[0], dict):
                it0 = items[0]
                t = str(it0.get("title") or "").strip()
                src = str(it0.get("source") or "").strip()
                link = str(it0.get("link") or "").strip()
                host = str(it0.get("host") or "").strip()
                if t:
                    ref_news = {
                        "source": src,
                        "title": t,
                        "link": link,
                        "host": host,
                        "hint": "読む→条件化（監視条件に変換）",
                    }
        except Exception:
            ref_news = None

        # --- 互換フィールド（今のテンプレを壊さない） ---
        headline = summary
        bullets: List[str] = []
        bullets.extend([f"・{x}" for x in reasons])
        # “参考”はbullets末尾に1個だけ
        if ref_news:
            bullets.append(f"・参考: {ref_news.get('source','')}「{ref_news.get('title','')}」→監視条件へ")
        bullets = bullets[:6]

        return {
            "title": "AI BRIEF",
            "status": "ok",
            "as_of": now_iso,

            # === 最終仕様（新） ===
            "summary": summary,              # 100〜160文字
            "reasons": reasons,              # max 5（アプリ内のみ）
            "concerns": concerns,            # max 2（アプリ内のみ）
            "escape": escape,                # 1行
            "reference_news": ref_news,      # 外部は参考枠（max1）

            # === 互換（旧） ===
            "headline": headline,
            "bullets": bullets,
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
            "reference_news": None,
            "headline": "（準備中）",
            "bullets": [],
            "error": str(e),
        }


# =========================
# Deck3: RISK（守り・制御）
# =========================
def _build_risk(user, assets: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    最終仕様へ寄せる（ただしテンプレ互換も残す）
    - Policy逸脱検知：ここでは“枠”だけ用意（Policy接続は次工程）
    - 含み損警戒ライン：assets未接続なら出せる範囲で
    - 建玉・信用使用率：UserSettingの credit_usage_pct を中心に
    - 推奨防御アクション（最大3）
    - 表示は 赤/黄/無色 の3段階のみ
    """
    now_iso = timezone.now().isoformat()

    try:
        from portfolio.models import UserSetting  # type: ignore

        setting, _ = UserSetting.objects.get_or_create(user=user)

        equity = _as_float(getattr(setting, "account_equity", 0), 0.0)
        risk_pct = _as_float(getattr(setting, "risk_pct", 0.0), 0.0)
        credit_usage_pct = _as_float(getattr(setting, "credit_usage_pct", 0.0), 0.0)

        risk_yen = int(round(equity * (risk_pct / 100.0))) if equity > 0 else 0

        # レベル判定（赤/黄/無色）
        # ここは“守り”なので辛めに
        level = "none"
        if equity <= 0 or risk_pct <= 0:
            level = "red"
        elif credit_usage_pct >= 85 or risk_pct >= 3.0:
            level = "yellow"

        # 警告（赤/黄のみ）
        alerts: List[Dict[str, Any]] = []

        if equity <= 0:
            alerts.append({
                "level": "red",
                "title": "口座残高（設定）が未入力",
                "desc": "数量計算とリスク上限が壊れる。まず口座残高（設定）を入れる。",
            })

        if risk_pct <= 0:
            alerts.append({
                "level": "red",
                "title": "1トレードのリスク％が未設定",
                "desc": "損失上限が無い状態。0.5〜1.5%の範囲で固定するのが安全。",
            })
        elif risk_pct >= 3.0:
            alerts.append({
                "level": "yellow",
                "title": "リスク％が高め",
                "desc": "連敗で資金が削れやすい。今日だけでもサイズを落として固定ルール優先。",
            })

        if credit_usage_pct >= 85:
            alerts.append({
                "level": "yellow",
                "title": "信用使用率が高い",
                "desc": "突っ込みすぎ領域。新規を減らして、建玉整理を優先。",
            })

        # Policy逸脱検知（次工程で本接続）
        policy_deviation = {
            "status": "todo",
            "level": "none",
            "message": "（次工程）Policy/逸脱ログと接続して検知します",
        }

        # 推奨防御アクション（最大3）
        actions: List[str] = []
        if level == "red":
            actions = [
                "新規エントリー停止（設定を埋めるまで）",
                "サイズ最小（0.25〜0.5R）に固定",
                "損切りを先に置けないなら入らない",
            ]
        elif level == "yellow":
            actions = [
                "新規を減らす（回数上限）",
                "サイズ半分（0.5R）",
                "建玉整理（含み損/回転の悪いものから）",
            ]
        else:
            actions = [
                "迷ったらサイズ半分（0.5R）",
                "取り返しトレード禁止（連敗時は回数上限）",
                "損切りを先に置けないなら入らない",
            ]

        actions = actions[:3]

        # 互換：今のテンプレ用 metrics/rules/notes も維持
        metrics: List[Dict[str, Any]] = []
        metrics.append({
            "label": "口座残高（設定）",
            "value": _fmt_yen(equity),
            "kind": "bad" if equity <= 0 else ("warn" if equity < 100_000 else "ok"),
            "sub": "UserSetting.account_equity",
        })
        metrics.append({
            "label": "1トレードのリスク％",
            "value": f"{risk_pct:.1f}%",
            "kind": "bad" if risk_pct <= 0 else ("warn" if risk_pct >= 3.0 else "ok"),
            "sub": "大きすぎると連敗で死ぬ",
        })
        metrics.append({
            "label": "1トレードの最大損失（目安）",
            "value": _fmt_yen(risk_yen),
            "kind": "bad" if risk_yen <= 0 else ("warn" if risk_yen >= 50_000 else "ok"),
            "sub": "エントリー前に“損切り幅×枚数”で一致させる",
        })
        metrics.append({
            "label": "信用余力の使用上限",
            "value": f"{credit_usage_pct:.0f}%",
            "kind": "warn" if credit_usage_pct >= 85 else "ok",
            "sub": "突っ込みすぎ防止",
        })

        notes: List[str] = []
        def _pair(name: str, lev: float, hc: float) -> str:
            return f"{name}: 倍率 {lev:.2f} / HC {int(round(hc*100)):d}%"

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

        rules = actions[:]  # 互換として今日のルール欄へ

        # alerts互換（kind=bad/warn/ok で見せる）
        alerts_compat: List[Dict[str, Any]] = []
        for a in alerts:
            kind = "bad" if a.get("level") == "red" else "warn"
            alerts_compat.append({
                "kind": kind,
                "title": a.get("title", ""),
                "desc": a.get("desc", ""),
            })

        return {
            "title": "RISK",
            "status": "ok",
            "as_of": now_iso,

            # === 最終仕様（新） ===
            "level": level,  # red / yellow / none
            "policy_deviation": policy_deviation,
            "alerts2": alerts,     # red/yellow only
            "actions": actions,    # max 3

            # === 互換（旧） ===
            "metrics": metrics,
            "alerts": alerts_compat,
            "rules": rules,
            "notes": notes,
        }

    except Exception as e:
        logger.exception("RISK build failed: %s", e)
        return {
            "title": "RISK",
            "status": "stub",
            "as_of": now_iso,

            "level": "red",
            "policy_deviation": {"status": "error", "level": "red", "message": "RISK build failed"},
            "alerts2": [{"level": "red", "title": "RISK生成失敗", "desc": str(e)}],
            "actions": ["新規停止", "ログ確認", "設定見直し"],

            "metrics": [],
            "alerts": [],
            "rules": [],
            "notes": [],
            "error": str(e),
        }


# =========================
# Deck4: MARKET（環境・空気）
# =========================
def _build_market(news_trends: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    外部ニュースは“ほどほど”に制限
    - NEWS 最大2
    - Trends 最大1
    ※ 最終仕様の「レジーム/ボラ/セクター相対強弱」は次工程で接続（今は枠だけ）
    """
    now_iso = timezone.now().isoformat()

    try:
        hot = _pick_hot_sectors_text(news_trends, limit=4)

        items = _as_list((news_trends or {}).get("items"))
        trends = _as_list((news_trends or {}).get("trends"))

        # 外部は控えめ（ほどほど）
        news_items: List[Dict[str, Any]] = []
        for it in items[:2]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            link = str(it.get("link") or "").strip()
            src = str(it.get("source") or "").strip()
            host = str(it.get("host") or "").strip()
            if not title:
                continue
            news_items.append({"title": title, "link": link, "source": src, "host": host})

        trend_items: List[Dict[str, Any]] = []
        for it in trends[:1]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            link = str(it.get("link") or "").strip()
            host = str(it.get("host") or "").strip()
            if not title:
                continue
            trend_items.append({"title": title, "link": link, "host": host})

        # “環境”の本体（次工程でデータを差し込む）
        regime = {"status": "todo", "label": "（準備中）"}
        volatility = {"status": "todo", "label": "（準備中）"}
        sector_strength = {
            "status": "light",
            "top": [str(x.get("sector")) for x in _as_list((news_trends or {}).get("sectors"))[:3] if isinstance(x, dict)],
            "note": "（暫定）ニュース頻度ベース。最終は相対強弱に置換。",
        }

        summary = "（準備中）"
        if hot:
            summary = hot
        elif news_items:
            summary = "ニュースから相場の“主語”だけ掴む（読み込まない）"
        elif trend_items:
            summary = "トレンドから“テーマ”だけ掴む（読み込まない）"
        else:
            summary = "相場環境は（準備中）— 今日はルール優先"

        return {
            "title": "MARKET",
            "status": "ok",
            "as_of": now_iso,

            # === 最終仕様（新） ===
            "regime": regime,
            "volatility": volatility,
            "sector_strength": sector_strength,

            # === 互換（旧） ===
            "summary": summary,
            "news": news_items,
            "trends": trend_items,
        }

    except Exception as e:
        logger.exception("MARKET build failed: %s", e)
        return {
            "title": "MARKET",
            "status": "stub",
            "as_of": now_iso,

            "regime": {"status": "error", "label": "（準備中）"},
            "volatility": {"status": "error", "label": "（準備中）"},
            "sector_strength": {"status": "error", "top": [], "note": ""},
            "summary": "（準備中）",
            "news": [],
            "trends": [],
            "error": str(e),
        }


# =========================
# Deck5: TODAY PLAN（今日の作戦）
# =========================
def _build_today_plan_from_assets(
    assets: Dict[str, Any],
    news_trends: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    最終仕様へ寄せる（ただしテンプレ互換 tasks も残す）
    - 今日のモード（短期×攻め/普通/守り/おまかせ）※次工程でPolicyから確定
    - Policy超要約（損切/利確/サイズ/対象）※次工程
    - 優先アクション3つ（アプリ内データ中心）
    - 自信度（簡易メーター）
    """
    now_iso = timezone.now().isoformat()

    try:
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

        # --- モード（暫定） ---
        # 最終は Policy 由来。今は assets進捗で “雰囲気” だけ。
        if goal_year_total <= 0:
            mode = {"period": "short", "style": "normal", "label": "短期×普通（暫定）"}
            confidence = 0.4
        else:
            if rem_month_total > 0:
                mode = {"period": "short", "style": "defense", "label": "短期×守り（未達対策）"}
                confidence = 0.55
            else:
                mode = {"period": "short", "style": "defense", "label": "短期×守り（達成圏維持）"}
                confidence = 0.65

        # --- Policy超要約（枠だけ） ---
        policy_digest = {
            "status": "todo",
            "sl": "（Policy接続予定）",
            "tp": "（Policy接続予定）",
            "size": "（Policy接続予定）",
            "universe": "（Policy接続予定）",
        }

        # --- 優先アクション（最大3） ---
        actions: List[str] = []
        if goal_year_total <= 0:
            actions = [
                "損切り幅/利確R/回数上限を1つ固定する",
                "同じ型だけで記録を厚くする（逸脱理由も残す）",
                "ニュースは読むな。見出しを監視条件に変換する",
            ]
            confidence = 0.45
        else:
            if rem_month_total > 0:
                actions = [
                    "狙う型を1つに絞る（取り返し禁止）",
                    "サイズ半分・回数上限で“負け方”を止める",
                    "未達の主因（証券会社別）を先に潰す",
                ]
                confidence = 0.58
            else:
                actions = [
                    "エントリー数を減らす（衝動禁止）",
                    "崩さない運用（ルール順守・サイズ固定）",
                    "勝ち/負けより“やらなかった判断”を1つ記録する",
                ]
                confidence = 0.68

        actions = actions[:3]

        # --- 互換：tasks も残す（今のテンプレのまま見える） ---
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
        else:
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

            # === 最終仕様（新） ===
            "mode": mode,                       # 例: {"period":"short","style":"defense","label":"短期×守り"}
            "policy_digest": policy_digest,     # 次工程でPolicy接続
            "actions": actions,                 # max 3
            "confidence": float(max(0.0, min(1.0, confidence))),

            # === 互換（旧） ===
            "tasks": tasks,
        }

    except Exception as e:
        logger.exception("TODAY PLAN build failed: %s", e)
        return {
            "title": "TODAY PLAN",
            "status": "error",
            "as_of": timezone.now().isoformat(),
            "mode": {"period": "short", "style": "normal", "label": "（準備中）"},
            "policy_digest": {"status": "error", "sl": "", "tp": "", "size": "", "universe": ""},
            "actions": [],
            "confidence": 0.0,
            "tasks": [],
            "error": str(e),
        }


# =========================
# validate
# =========================
def _validate_decks_shape(decks: Any) -> Tuple[bool, str]:
    if not isinstance(decks, list) or len(decks) == 0:
        return False, "decks is not list or empty"
    for x in decks:
        if not isinstance(x, dict):
            return False, "decks contains non-dict item"
        if "key" not in x or "title" not in x or "payload" not in x:
            return False, "decks item missing key/title/payload"
    return True, ""


# =========================
# main upsert
# =========================
@transaction.atomic
def upsert_today_snapshot(user) -> None:
    """
    今日分の HomeDeckSnapshot を生成して保存（上書き）

    - 6:30 cron 実行前提：NEWSは force_refresh=True（その朝の固定を作る）
    - NEWS取得失敗/空っぽでも “直前の保存” で補完して、Homeが崩れない
    - 外部ニュースは “ほどほど”（AI BRIEF: 参考1件 / MARKET: NEWS2+Trends1）
    - 最終仕様へ寄せたpayloadを追加（ただし既存テンプレ互換も維持）
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

    # --- NEWS & TRENDS（6:30固定生成） ---
    news_trends = _build_news_trends(force_refresh=True, user=user)

    # --- AI BRIEF / RISK / MARKET / TODAY PLAN ---
    ai_brief = _build_ai_brief(assets, news_trends=news_trends)
    risk = _build_risk(user, assets=assets)
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