from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

from django.db.models import Sum, Max
from django.utils.timezone import now as dj_now
from django.contrib.auth import get_user_model

from portfolio.models import Holding
from portfolio.models_cash import BrokerAccount, CashLedger, MarginState
from advisor.models import WatchEntry

User = get_user_model()
JST = timezone(timedelta(hours=9))

_HAS_CACHE = False
try:
    from advisor.models_cache import PriceCache, BoardCache
    _HAS_CACHE = True
except Exception:
    PriceCache = None  # type: ignore
    BoardCache = None  # type: ignore
    _HAS_CACHE = False

_HAS_TREND = False
try:
    from advisor.models_trend import TrendResult
    _HAS_TREND = True
except Exception:
    TrendResult = None  # type: ignore
    _HAS_TREND = False


# =============== helpers ===============
def _jst_now() -> datetime:
    return dj_now().astimezone(JST)

def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def _price_from_cache_or(value_hint: Optional[int], ticker: str) -> Optional[int]:
    if _HAS_CACHE and PriceCache is not None:
        pc = PriceCache.objects.filter(ticker=ticker.upper()).first()
        if pc and pc.last_price is not None:
            try:
                return int(pc.last_price)
            except Exception:
                pass
    if value_hint is not None:
        try:
            return int(value_hint)
        except Exception:
            pass
    return None

def _latest_margin_available_funds(user) -> Optional[int]:
    qs = (
        MarginState.objects
        .filter(account__broker__isnull=False,
                account__account_type="信用",
                account__currency="JPY",
                account__in=BrokerAccount.objects.filter(
                    account_type="信用", currency="JPY"
                ))
    )
    if not qs.exists():
        return None
    latest_per_acct = qs.values("account_id").annotate(as_of_max=Max("as_of"))
    acct_to_latest_date = {row["account_id"]: row["as_of_max"] for row in latest_per_acct}
    total = 0
    for acct_id, as_of in acct_to_latest_date.items():
        st = qs.filter(account_id=acct_id, as_of=as_of).first()
        if not st:
            continue
        total += int(st.available_funds)
    return max(0, total)

def _cash_fallback_credit(user) -> int:
    accts = BrokerAccount.objects.filter(account_type="信用", currency="JPY")
    total = 0
    for a in accts:
        bal = _safe_int(a.opening_balance, 0)
        led = CashLedger.objects.filter(account=a).aggregate(s=Sum("amount"))["s"] or 0
        total += int(bal + led)
    return max(0, total)

def _resolve_credit_balance(user) -> int:
    m = _latest_margin_available_funds(user)
    if m is not None:
        return max(0, int(m))
    return _cash_fallback_credit(user)

def _attach_sizing(items: List[Dict[str, Any]], credit_balance: int, risk_per_trade: float = 0.01) -> None:
    for it in items:
        entry = _safe_int(it.get("entry_price_hint") or 3000, 3000)
        sl_pct = float(it.get("targets", {}).get("sl_pct") or 0.02)
        sl_price = _safe_int(it.get("targets", {}).get("sl_price") or round(entry * (1 - sl_pct)), 1)
        stop_value = max(1, entry - sl_price)
        risk_budget = max(1, int(round(credit_balance * risk_per_trade)))
        shares = risk_budget // stop_value if stop_value > 0 else 0
        need_cash = shares * entry if shares > 0 else 0

        win_prob = float(it.get("ai", {}).get("win_prob") or 0.6)
        tp_prob = max(0.0, min(1.0, win_prob * 0.46))
        sl_prob = max(0.0, min(1.0, (1 - win_prob) * 0.30))

        it.setdefault("ai", {})
        it["ai"].update({"tp_prob": tp_prob, "sl_prob": sl_prob})
        it["sizing"] = {
            "credit_balance": int(credit_balance),
            "risk_per_trade": float(risk_per_trade),
            "position_size_hint": int(shares),
            "need_cash": int(need_cash),
        }

def _load_board_cache(user) -> Optional[Dict[str, Any]]:
    if not (_HAS_CACHE and BoardCache is not None):
        return None
    bc = (
        BoardCache.objects.filter(user=user).first()
        or BoardCache.objects.filter(user__isnull=True).first()
    )
    if not bc:
        return None
    try:
        if bc.is_fresh and bc.payload and bc.payload.get("highlights"):
            payload = dict(bc.payload)
            meta = payload.setdefault("meta", {})
            meta["live"] = True
            mv = meta.get("model_version", "")
            meta["model_version"] = f"{mv}+cached" if mv else "cached"
            return payload
    except Exception:
        pass
    return None

def _save_board_cache(user, payload: Dict[str, Any], ttl_minutes: int = 180) -> None:
    if not (_HAS_CACHE and BoardCache is not None):
        return
    try:
        if not payload.get("highlights"):
            return
        BoardCache.objects.create(
            user=user if (hasattr(user, "is_authenticated") and user.is_authenticated) else None,
            payload=payload,
            generated_at=dj_now(),
            ttl_minutes=ttl_minutes,
            note="auto",
        )
    except Exception:
        pass

# ---- ここが“JS安全化”の要：必ず非null化して返す ----
def _normalize_item(it: Dict[str, Any]) -> Dict[str, Any]:
    name = (it.get("name") or it.get("ticker") or "").strip()
    ticker = (it.get("ticker") or "").strip().upper()
    seg = it.get("segment") or ""
    act = it.get("action") or ""
    wk = (it.get("weekly_trend") or "flat").lower()
    overall = _safe_int(it.get("overall_score") or 0, 0)
    entry = _safe_int(it.get("entry_price_hint") or 3000, 3000)

    ai = it.get("ai") or {}
    win_prob = float(ai.get("win_prob") or 0.6)
    tp_prob = float(ai.get("tp_prob") or 0.0)
    sl_prob = float(ai.get("sl_prob") or 0.0)

    theme = it.get("theme") or {}
    theme_id = theme.get("id") or "generic"
    theme_label = theme.get("label") or "—"
    theme_score = float(theme.get("score") or 0.55)

    t = it.get("targets") or {}
    tp_pct = float(t.get("tp_pct") or 0.10)
    sl_pct = float(t.get("sl_pct") or 0.03)
    tp_price = _safe_int(t.get("tp_price") or round(entry * (1 + tp_pct)), 1)
    sl_price = _safe_int(t.get("sl_price") or round(entry * (1 - sl_pct)), 1)

    sizing = it.get("sizing") or {}
    pos = _safe_int(sizing.get("position_size_hint") or 0, 0)
    need_cash = _safe_int(sizing.get("need_cash") or 0, 0)
    credit_balance = _safe_int(sizing.get("credit_balance") or 0, 0)
    rpt = float(sizing.get("risk_per_trade") or 0.01)

    reasons = it.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)]

    return {
        "ticker": ticker,
        "name": name,
        "segment": seg,
        "action": act,
        "reasons": [str(r) for r in reasons],
        "ai": {"win_prob": float(win_prob), "tp_prob": float(tp_prob), "sl_prob": float(sl_prob), "size_mult": float((ai.get("size_mult") or 1.0))},
        "theme": {"id": str(theme_id), "label": str(theme_label), "score": float(theme_score)},
        "weekly_trend": wk if wk in ("up", "down", "flat") else "flat",
        "overall_score": int(overall),
        "entry_price_hint": int(entry),
        "targets": {
            "tp": f"目標 +{int(tp_pct*100)}%",
            "sl": f"損切り -{int(sl_pct*100)}%",
            "tp_pct": float(tp_pct), "sl_pct": float(sl_pct),
            "tp_price": int(tp_price), "sl_price": int(sl_price),
        },
        "sizing": {
            "credit_balance": int(credit_balance),
            "risk_per_trade": float(rpt),
            "position_size_hint": int(pos),
            "need_cash": int(need_cash),
        },
    }

# =============== candidates ===============
def _trend_candidates(user) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not (_HAS_TREND and TrendResult is not None):
        return items

    latest_rows: List[Tuple[str, int]] = list(
        TrendResult.objects.filter(user=user)
        .values_list("ticker")
        .annotate(lat_id=Max("id"))
    )
    if not latest_rows:
        return items

    ids = [row[1] for row in latest_rows if row[1]]
    qs = TrendResult.objects.filter(id__in=ids).order_by("-overall_score", "-updated_at")[:20]

    for tr in qs:
        last = _price_from_cache_or(tr.entry_price_hint, tr.ticker) or tr.close_price or 3000
        try:
            last = int(round(float(last)))
        except Exception:
            last = 3000

        win_prob = float(tr.win_prob or 0.62)
        theme = float(tr.theme_score or 0.55)
        slope = float(tr.slope_annual or 0.0)
        wk = (tr.weekly_trend or "flat").lower()
        wk_adj = 0.05 if wk == "up" else (-0.05 if wk == "down" else 0.0)

        overall = int(round((win_prob * 0.6 + theme * 0.25 + (0.5 + wk_adj) * 0.10 + max(-0.1, min(0.1, slope)) * 0.05) * 100))

        tp_pct = 0.10; sl_pct = 0.03
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        items.append({
            "ticker": tr.ticker.upper(),
            "name": tr.name or tr.ticker,
            "segment": "トレンド",
            "action": "買い候補" if win_prob >= 0.6 else "様子見",
            "reasons": ["TrendResultベース", f"信頼度{int(round((tr.confidence or 0.5)*100))}%", f"slope:{round(slope*100,2)}%/yr"],
            "ai": {"win_prob": win_prob, "size_mult": 1.0},
            "theme": {"id": "trend", "label": tr.theme_label or "—", "score": theme},
            "weekly_trend": wk,
            "overall_score": overall,
            "entry_price_hint": last,
            "targets": {"tp_pct": tp_pct, "sl_pct": sl_pct, "tp_price": tp_price, "sl_price": sl_price},
        })
    items.sort(key=lambda x: (x.get("overall_score") or 0), reverse=True)
    return items

def _watch_candidates(user) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    qs = (
        WatchEntry.objects
        .filter(user=user, status=WatchEntry.STATUS_ACTIVE)
        .order_by("-updated_at")[:12]
    )
    for w in qs:
        last = _price_from_cache_or(w.entry_price_hint, w.ticker) or 3000
        tp_pct = float(w.tp_pct or 0.06)
        sl_pct = float(w.sl_pct or 0.02)
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        ai_prob = float(w.ai_win_prob or 0.62)
        theme_score = float(w.theme_score or 0.55)

        wk = (w.weekly_trend or "").strip().lower()
        if _HAS_TREND and TrendResult is not None:
            tr = (
                TrendResult.objects.filter(user=user, ticker=w.ticker.upper())
                .order_by("-asof").first()
            )
            if tr:
                wk = (tr.weekly_trend or wk or "flat").lower()
                adjust = max(-0.05, min(0.05, (float(tr.confidence or 0.5) - 0.5) * 0.10))
                ai_prob = min(0.95, max(0.05, ai_prob + adjust))

        if wk not in ("up", "down", "flat"):
            tmp = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
            wk = "up" if tmp >= 65 else ("flat" if tmp >= 50 else "down")

        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))

        items.append({
            "ticker": w.ticker.upper(),
            "name": (w.name or w.ticker),
            "segment": "監視",
            "action": "買い候補" if ai_prob >= 0.6 else "様子見",
            "reasons": w.reason_details or ((w.reason_summary or "").split("/") if w.reason_summary else []),
            "ai": {"win_prob": ai_prob, "size_mult": 1.0},
            "theme": {"id": "auto", "label": (w.theme_label or "テーマ"), "score": theme_score},
            "weekly_trend": wk,
            "overall_score": overall,
            "entry_price_hint": int(last),
            "targets": {"tp_pct": tp_pct, "sl_pct": sl_pct, "tp_price": tp_price, "sl_price": sl_price},
        })
    return items

def _holding_candidates(user) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    qs = Holding.objects.filter(user=user).order_by("-updated_at")[:12]
    for h in qs:
        base_price = None
        if h.last_price is not None:
            try:
                base_price = int(round(float(h.last_price)))
            except Exception:
                base_price = None
        last = _price_from_cache_or(base_price, h.ticker) or 3000

        tp_pct = 0.10; sl_pct = 0.03
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        ai_prob = 0.63
        theme_score = 0.55

        wk = None
        if _HAS_TREND and TrendResult is not None:
            tr = (
                TrendResult.objects.filter(user=user, ticker=h.ticker.upper())
                .order_by("-asof").first()
            )
            if tr:
                wk = (tr.weekly_trend or "flat").lower()
                adjust = max(-0.05, min(0.05, (float(tr.confidence or 0.5) - 0.5) * 0.10))
                ai_prob = min(0.95, max(0.05, ai_prob + adjust))

        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        if wk not in ("up", "down", "flat"):
            wk = "up" if overall >= 65 else ("flat" if overall >= 50 else "down")

        items.append({
            "ticker": h.ticker.upper(),
            "name": h.name or h.ticker,
            "segment": "保有（補完）",
            "action": "買い候補（簡易）",
            "reasons": ["既存保有/監視から抽出", "価格は最新値ベース", "暫定パラメータ"],
            "ai": {"win_prob": ai_prob, "size_mult": 1.0},
            "theme": {"id": "generic", "label": h.sector or "—", "score": theme_score},
            "weekly_trend": wk,
            "overall_score": overall,
            "entry_price_hint": int(last),
            "targets": {"tp_pct": tp_pct, "sl_pct": sl_pct, "tp_price": tp_price, "sl_price": sl_price},
        })
    return items


# =============== public ===============
def build_board(user, *, use_cache: bool = True, min_items: int = 5) -> Dict[str, Any]:
    if use_cache:
        cached = _load_board_cache(user)
        if cached is not None:
            return cached

    now = _jst_now()
    credit = _resolve_credit_balance(user)
    risk_per_trade = 0.01

    items: List[Dict[str, Any]] = _trend_candidates(user)
    if len(items) < min_items:
        items += _watch_candidates(user)
    if len(items) < min_items:
        items += _holding_candidates(user)

    items = items[:min_items]
    _attach_sizing(items, credit_balance=credit, risk_per_trade=risk_per_trade)

    # ★ ここで全件を“JS安全化”して返す
    items = [_normalize_item(it) for it in items]

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": now.replace(second=0, microsecond=0).isoformat(),
            "model_version": "v0.4-trend-first+cached",
            "adherence_week": 0.84,
            "regime": {"trend_prob": 0.55, "range_prob": 0.45, "nikkei": "→", "topix": "→"},
            "scenario": "TrendResult最優先で今日の候補を生成（監視/保有で補完）",
            "pairing": {"id": 2, "label": "順張り・短中期"},
            "self_mirror": {"recent_drift": "—"},
            "credit_balance": int(credit),
            "live": True,
        },
        "theme": {
            "week": now.strftime("%Y-W%V"),
            "top3": [
                {"id": "trend",    "label": "トレンド",  "score": 0.60},
                {"id": "generic",  "label": "監視/保有", "score": 0.56},
                {"id": "generic2", "label": "セクター",  "score": 0.52},
            ],
        },
        "highlights": items,
    }

    if use_cache and data.get("highlights"):
        _save_board_cache(user, data, ttl_minutes=180)
    return data