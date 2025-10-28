# advisor/services/board_source.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

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


# ---------- helpers ----------
def _jst_now() -> datetime:
    return dj_now().astimezone(JST)

def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def _holding_name_map(user) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for h in Holding.objects.filter(user=user).only("ticker", "name"):
        if h.ticker and h.name:
            m[h.ticker.upper()] = h.name
    return m

def _latest_margin_available_funds(user) -> Optional[int]:
    qs = (
        MarginState.objects
        .filter(account__broker__isnull=False,
                account__account_type="信用",
                account__currency="JPY",
                account__in=BrokerAccount.objects.filter(account_type="信用", currency="JPY"))
    )
    if not qs.exists():
        return None
    latest_per_acct = qs.values("account_id").annotate(as_of_max=Max("as_of"))
    acct_to_latest_date = {row["account_id"]: row["as_of_max"] for row in latest_per_acct}
    total = 0
    for acct_id, as_of in acct_to_latest_date.items():
        st = qs.filter(account_id=acct_id, as_of=as_of).first()
        if st:
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
    return max(0, int(m)) if m is not None else _cash_fallback_credit(user)

def _price_from_cache_or(value_hint: Optional[int], ticker: str) -> Optional[int]:
    if _HAS_CACHE and PriceCache is not None:
        pc = PriceCache.objects.filter(ticker=ticker.upper()).first()
        if pc:
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

def _load_board_cache(user) -> Optional[Dict[str, Any]]:
    if not (_HAS_CACHE and BoardCache is not None):
        return None
    bc = (BoardCache.objects.filter(user=user).first()
          or BoardCache.objects.filter(user__isnull=True).first())
    if not bc:
        return None
    try:
        if bc.is_fresh:
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
        BoardCache.objects.create(
            user=user if (hasattr(user, "is_authenticated") and user.is_authenticated) else None,
            payload=payload,
            generated_at=dj_now(),
            ttl_minutes=ttl_minutes,
            note="auto",
        )
    except Exception:
        pass

def _trend_for(ticker: str) -> Optional[Dict[str, float]]:
    if not _HAS_TREND or TrendResult is None:
        return None
    row = (TrendResult.objects
           .filter(ticker=(ticker or "").upper())
           .order_by("-asof")
           .first())
    if not row:
        return None
    return {
        "weekly_trend": (row.weekly_trend or "flat"),
        "confidence": float(getattr(row, "confidence", 0.5) or 0.5),
        "slope_annual": float(getattr(row, "slope_annual", 0.0) or 0.0),
        "name": (row.name or "").strip(),
        "entry_hint": int(row.entry_price_hint or 0) or None,
    }


# ---------- candidates ----------
def _trend_candidates(user, top_n: int = 5) -> List[Dict[str, Any]]:
    """
    TrendResultベースの候補（最優先）。overall_scoreが無くても slope_annual × confidence で暫定点を作る。
    """
    if not _HAS_TREND or TrendResult is None:
        return []
    hold_names = _holding_name_map(user)

    rows = (TrendResult.objects
            .filter(user=user)
            .order_by("-asof", "-overall_score", "-updated_at")[:50])

    items: List[Dict[str, Any]] = []
    for r in rows:
        name = (r.name or "").strip() or hold_names.get(r.ticker.upper()) or r.ticker.upper()
        entry = r.entry_price_hint or r.close_price or 3000
        tp_pct, sl_pct = 0.10, 0.03
        tp_price = int(round(entry * (1 + tp_pct)))
        sl_price = int(round(entry * (1 - sl_pct)))

        conf = float(getattr(r, "confidence", 0.5) or 0.5)
        slope = float(getattr(r, "slope_annual", 0.0) or 0.0)
        # 暫定スコア（-0.5〜+0.5を0〜100に）
        s = max(-0.5, min(0.5, slope))
        overall = int(round((s + 0.5) * 100 * (0.6 + 0.4 * conf)))

        items.append({
            "ticker": r.ticker.upper(),
            "name": name,
            "segment": "トレンド（自動）",
            "action": "買い候補",
            "reasons": ["TrendResultベース", f"信頼{int(conf*100)}%", f"slope≈{round(slope*100,1)}%/yr"],
            "ai": {"win_prob": 0.62, "size_mult": 1.0},
            "theme": {"id": "trend", "label": "トレンド", "score": 0.6},
            "weekly_trend": r.weekly_trend or "flat",
            "overall_score": overall,
            "entry_price_hint": int(entry),
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
        })
        if len(items) >= top_n:
            break
    return items

def _watch_candidates(user) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    qs = (WatchEntry.objects
          .filter(status=WatchEntry.STATUS_ACTIVE)
          .order_by("-updated_at")[:12])
    for w in qs:
        last = _price_from_cache_or(w.entry_price_hint, w.ticker) or 3000
        tp_pct = float(w.tp_pct or 0.06); sl_pct = float(w.sl_pct or 0.02)
        tp_price = int(round(last * (1 + tp_pct))); sl_price = int(round(last * (1 - sl_pct)))
        ai_prob = float(w.ai_win_prob or 0.62); theme_score = float(w.theme_score or 0.55)
        wk = (w.weekly_trend or "").strip().lower()
        tr = _trend_for(w.ticker)
        if tr:
            wk = (tr["weekly_trend"] or wk).lower()
            adjust = max(-0.05, min(0.05, (tr["confidence"] - 0.5) * 0.10))
            ai_prob = min(0.95, max(0.05, ai_prob + adjust))
        if wk not in ("up", "down", "flat"):
            overall_tmp = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
            wk = "up" if overall_tmp >= 65 else ("flat" if overall_tmp >= 50 else "down")
        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        items.append({
            "ticker": w.ticker,
            "name": w.name or w.ticker,
            "segment": "監視",
            "action": "買い候補" if ai_prob >= 0.6 else "様子見",
            "reasons": w.reason_details or ((w.reason_summary or "").split("/") if w.reason_summary else []),
            "ai": {"win_prob": ai_prob, "size_mult": 1.0},
            "theme": {"id": "auto", "label": (w.theme_label or "テーマ"), "score": theme_score},
            "weekly_trend": wk,
            "overall_score": overall,
            "entry_price_hint": last,
            "targets": {"tp": f"目標 +{int(tp_pct*100)}%", "sl": f"損切り -{int(sl_pct*100)}%",
                        "tp_pct": tp_pct, "sl_pct": sl_pct, "tp_price": tp_price, "sl_price": sl_price},
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
        tp_pct, sl_pct = 0.10, 0.03
        tp_price = int(round(last * (1 + tp_pct))); sl_price = int(round(last * (1 - sl_pct)))
        ai_prob = 0.63; theme_score = 0.55
        tr = _trend_for(h.ticker); wk = (tr["weekly_trend"] if tr else None) or "flat"
        if tr:
            adjust = max(-0.05, min(0.05, (tr["confidence"] - 0.5) * 0.10))
            ai_prob = min(0.95, max(0.05, ai_prob + adjust))
        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        items.append({
            "ticker": h.ticker.upper(),
            "name": h.name or h.ticker,
            "segment": "中期（20〜45日）",
            "action": "買い候補（簡易）",
            "reasons": ["既存保有/監視から抽出", "価格は最新値ベース", "暫定パラメータ"],
            "ai": {"win_prob": ai_prob, "size_mult": 1.0},
            "theme": {"id": "generic", "label": h.sector or "—", "score": theme_score},
            "weekly_trend": wk,
            "overall_score": overall,
            "entry_price_hint": last,
            "targets": {"tp": f"目標 +{int(tp_pct*100)}%", "sl": f"損切り -{int(sl_pct*100)}%",
                        "tp_pct": tp_pct, "sl_pct": sl_pct, "tp_price": tp_price, "sl_price": sl_price},
        })
    return items

def _attach_sizing(items: List[Dict[str, Any]], credit_balance: int, risk_per_trade: float = 0.01) -> None:
    for it in items:
        entry = int(it.get("entry_price_hint") or 3000)
        sl_price = int(it["targets"].get("sl_price") or max(1, int(round(entry * (1 - float(it["targets"].get("sl_pct") or 0.02))))))
        stop_value = max(1, entry - sl_price)
        risk_budget = max(1, int(round(credit_balance * risk_per_trade)))
        shares = risk_budget // stop_value if stop_value > 0 else 0
        need_cash = shares * entry if shares > 0 else None
        win_prob = float(it.get("ai", {}).get("win_prob") or 0.6)
        tp_prob = max(0.0, min(1.0, win_prob * 0.46))
        sl_prob = max(0.0, min(1.0, (1 - win_prob) * 0.30))
        it["sizing"] = {"credit_balance": credit_balance, "risk_per_trade": risk_per_trade,
                        "position_size_hint": shares if shares > 0 else None, "need_cash": need_cash}
        it["ai"] = {**it.get("ai", {}), "tp_prob": tp_prob, "sl_prob": sl_prob}


# ---------- public entry ----------
def build_board(user, *, use_cache: bool = True) -> Dict[str, Any]:
    if use_cache:
        cached = _load_board_cache(user)
        if cached is not None:
            return cached

    now = _jst_now()
    credit = _resolve_credit_balance(user)
    risk_per_trade = 0.01

    items: List[Dict[str, Any]] = []
    # 1) Trend最優先
    items += _trend_candidates(user, top_n=5)
    # 2) 足りなければ watch/holding で補完
    if len(items) < 5:
        items += _watch_candidates(user)
    if len(items) < 5:
        items += _holding_candidates(user)
    items = items[:5]

    _attach_sizing(items, credit_balance=credit, risk_per_trade=risk_per_trade)

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": now.replace(second=0, microsecond=0).isoformat(),
            "model_version": "v0.4-trend-first+cached",
            "adherence_week": 0.84,
            "regime": {"trend_prob": 0.55, "range_prob": 0.45, "nikkei": "→", "topix": "→"},
            "scenario": "TrendResult優先で今日の候補を生成",
            "pairing": {"id": 2, "label": "順張り・短中期"},
            "self_mirror": {"recent_drift": "—"},
            "credit_balance": int(credit),
            "live": True,
        },
        "theme": {
            "week": now.strftime("%Y-W%V"),
            "top3": [
                {"id": "trend",   "label": "トレンド", "score": 0.60},
                {"id": "generic", "label": "監視テーマ", "score": 0.56},
                {"id": "generic2","label": "セクター", "score": 0.52},
            ],
        },
        "highlights": items,
    }

    if use_cache:
        _save_board_cache(user, data, ttl_minutes=180)

    return data