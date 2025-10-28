from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from django.db.models import Sum, Max
from django.utils.timezone import now as dj_now
from django.contrib.auth import get_user_model

# ポートフォリオ側
from portfolio.models import Holding
from portfolio.models_cash import BrokerAccount, CashLedger, MarginState
# アドバイザー側
from advisor.models import WatchEntry

User = get_user_model()
JST = timezone(timedelta(hours=9))

# ---- キャッシュ層 ------------------------------------------------
_HAS_CACHE = False
try:
    from advisor.models_cache import PriceCache, BoardCache
    _HAS_CACHE = True
except Exception:
    PriceCache = None
    BoardCache = None
    _HAS_CACHE = False

# ---- トレンド層 ------------------------------------------------
_HAS_TREND = False
try:
    from advisor.models_trend import TrendResult
    _HAS_TREND = True
except Exception:
    TrendResult = None
    _HAS_TREND = False


def _jst_now() -> datetime:
    return dj_now().astimezone(JST)


# ---------- PriceCache参照 ----------
def _price_from_cache_or(value_hint: Optional[int], ticker: str) -> Optional[int]:
    if _HAS_CACHE and PriceCache is not None:
        pc = PriceCache.objects.filter(ticker=ticker.upper()).first()
        if pc and pc.last_price:
            try:
                return int(pc.last_price)
            except Exception:
                pass
    if value_hint:
        try:
            return int(value_hint)
        except Exception:
            pass
    return None


# ---------- 信用余力 ----------
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
        if st:
            total += int(st.available_funds)
    return max(0, total)

def _cash_fallback_credit(user) -> int:
    accts = BrokerAccount.objects.filter(account_type="信用", currency="JPY")
    total = 0
    for a in accts:
        bal = int(a.opening_balance or 0)
        led = CashLedger.objects.filter(account=a).aggregate(s=Sum("amount"))["s"] or 0
        total += int(bal + led)
    return max(0, total)

def _resolve_credit_balance(user) -> int:
    m = _latest_margin_available_funds(user)
    return m if m is not None else _cash_fallback_credit(user)


# ---------- Trend候補 ----------
def _trend_candidates(user) -> List[Dict[str, Any]]:
    if not _HAS_TREND or TrendResult is None:
        return []
    latest = TrendResult.objects.filter(user=user).order_by("-asof").values_list("asof", flat=True).first()
    if not latest:
        return []
    rows = TrendResult.objects.filter(user=user, asof=latest).order_by("-overall_score", "-slope_annual")[:12]
    items = []
    for r in rows:
        last = int(r.entry_price_hint or r.close_price or 3000)
        tp_pct, sl_pct = 0.1, 0.03
        items.append({
            "ticker": r.ticker,
            "name": r.name or r.ticker,
            "segment": f"Trend({r.window_days}d)",
            "action": "買い候補" if (r.overall_score or 60) >= 60 else "様子見",
            "reasons": [f"trend={r.weekly_trend}", f"slope={round((r.slope_annual or 0)*100,1)}%", f"conf={round((r.confidence or 0)*100)}%"],
            "ai": {"win_prob": float(r.win_prob or 0.62), "size_mult": float(r.size_mult or 1.0)},
            "theme": {"id": "trend", "label": r.theme_label or "—", "score": float(r.theme_score or 0.55)},
            "weekly_trend": r.weekly_trend or "flat",
            "overall_score": int(r.overall_score or 62),
            "entry_price_hint": last,
            "targets": {
                "tp": "目標 +10%", "sl": "損切り -3%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": int(round(last*(1+tp_pct))),
                "sl_price": int(round(last*(1-sl_pct))),
            },
        })
    return items


# ---------- WatchEntry候補 ----------
def _watch_candidates(user) -> List[Dict[str, Any]]:
    qs = WatchEntry.objects.filter(status=WatchEntry.STATUS_ACTIVE).order_by("-updated_at")[:12]
    items = []
    for w in qs:
        last = _price_from_cache_or(w.entry_price_hint, w.ticker) or 3000
        tp_pct, sl_pct = 0.06, 0.02
        tp_price = int(round(last*(1+tp_pct)))
        sl_price = int(round(last*(1-sl_pct)))
        ai_prob = float(w.ai_win_prob or 0.62)
        theme_score = float(w.theme_score or 0.55)
        overall = int(round((ai_prob*0.7 + theme_score*0.3)*100))
        items.append({
            "ticker": w.ticker,
            "name": w.name or w.ticker,
            "segment": "監視",
            "action": "買い候補" if ai_prob>=0.6 else "様子見",
            "ai": {"win_prob": ai_prob, "size_mult": 1.0},
            "theme": {"id": "auto", "label": (w.theme_label or "テーマ"), "score": theme_score},
            "weekly_trend": w.weekly_trend or "flat",
            "overall_score": overall,
            "entry_price_hint": last,
            "targets": {"tp": f"+{int(tp_pct*100)}%", "sl": f"-{int(sl_pct*100)}%", "tp_price": tp_price, "sl_price": sl_price},
        })
    return items


# ---------- Holding候補 ----------
def _holding_candidates(user) -> List[Dict[str, Any]]:
    qs = Holding.objects.all().order_by("-updated_at")[:12]
    items = []
    for h in qs:
        last = _price_from_cache_or(h.last_price, h.ticker) or 3000
        tp_pct, sl_pct = 0.1, 0.03
        tp_price = int(round(last*(1+tp_pct)))
        sl_price = int(round(last*(1-sl_pct)))
        ai_prob = 0.63
        theme_score = 0.55
        overall = int(round((ai_prob*0.7 + theme_score*0.3)*100))
        items.append({
            "ticker": h.ticker.upper(),
            "name": h.name or h.ticker,
            "segment": "中期",
            "action": "買い候補",
            "ai": {"win_prob": ai_prob},
            "theme": {"id": "generic", "label": h.sector or "—", "score": theme_score},
            "weekly_trend": "flat",
            "overall_score": overall,
            "entry_price_hint": last,
            "targets": {"tp": f"+{int(tp_pct*100)}%", "sl": f"-{int(sl_pct*100)}%", "tp_price": tp_price, "sl_price": sl_price},
        })
    return items


# ---------- build_board ----------
def build_board(user, *, use_cache: bool=True) -> Dict[str, Any]:
    now = _jst_now()
    credit = _resolve_credit_balance(user)
    items = _trend_candidates(user)
    if len(items) < 5:
        items += _watch_candidates(user)
    if len(items) < 5:
        items += _holding_candidates(user)
    items = items[:5]

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": now.replace(second=0, microsecond=0).isoformat(),
            "model_version": "v0.4-trend-priority",
            "credit_balance": int(credit),
            "live": True,
        },
        "theme": {
            "week": now.strftime("%Y-W%V"),
        },
        "highlights": items,
    }
    return data