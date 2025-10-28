from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from django.db.models import Sum, Max
from django.utils.timezone import now as dj_now
from django.contrib.auth import get_user_model

# ポートフォリオ側の実データ
from portfolio.models import Holding
from portfolio.models_cash import BrokerAccount, CashLedger, MarginState

# アドバイザー側
from advisor.models import WatchEntry
from advisor.models_trend import TrendResult  # ←★ 新トレンドモデルを優先利用

User = get_user_model()
JST = timezone(timedelta(hours=9))

# ---- キャッシュ層（存在しなくても動くように安全に読み込み） -----------------
_HAS_CACHE = False
try:
    from advisor.models_cache import PriceCache, BoardCache
    _HAS_CACHE = True
except Exception:
    PriceCache = None
    BoardCache = None
    _HAS_CACHE = False


# ---------- helpers ----------
def _jst_now() -> datetime:
    return dj_now().astimezone(JST)

def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default

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
    acct_to_latest_date = {r["account_id"]: r["as_of_max"] for r in latest_per_acct}
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
    bc = (
        BoardCache.objects.filter(user=user).first()
        or BoardCache.objects.filter(user__isnull=True).first()
    )
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


# ---------- 候補生成 ----------
def _trend_candidates(user) -> List[Dict[str, Any]]:
    """
    TrendResultからAIが選んだ最新候補を優先抽出。
    """
    items: List[Dict[str, Any]] = []
    latest_date = TrendResult.objects.filter(user=user).aggregate(Max("asof"))["asof__max"]
    if not latest_date:
        return items

    qs = (
        TrendResult.objects
        .filter(user=user, asof=latest_date)
        .order_by("-overall_score")[:8]
    )
    for tr in qs:
        last = _price_from_cache_or(tr.entry_price_hint, tr.ticker) or tr.close_price or 3000
        tp_pct = 0.1
        sl_pct = 0.03
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        items.append({
            "ticker": tr.ticker,
            "name": tr.name or tr.ticker,
            "segment": "AIトレンド",
            "action": "強気" if tr.weekly_trend == "up" else ("中立" if tr.weekly_trend == "flat" else "警戒"),
            "reasons": [f"トレンド: {tr.weekly_trend}", f"スコア: {tr.overall_score}", f"信頼度: {tr.confidence:.2f}"],
            "ai": {
                "win_prob": float(tr.win_prob or 0.6),
                "size_mult": float(tr.size_mult or 1.0),
                "confidence": float(tr.confidence or 0.5),
            },
            "theme": {
                "id": "trend",
                "label": tr.theme_label or "テーマ不明",
                "score": float(tr.theme_score or 0.5),
            },
            "weekly_trend": tr.weekly_trend,
            "overall_score": int(tr.overall_score or 50),
            "entry_price_hint": int(last),
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
            "slope_annual": tr.slope_annual,
            "window_days": tr.window_days,
        })
    return items


def _watch_candidates(user) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    qs = WatchEntry.objects.filter(status=WatchEntry.STATUS_ACTIVE).order_by("-updated_at")[:8]
    for w in qs:
        last = _price_from_cache_or(w.entry_price_hint, w.ticker) or 3000
        tp_pct, sl_pct = 0.06, 0.02
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))
        ai_prob = float(w.ai_win_prob or 0.62)
        theme_score = float(w.theme_score or 0.55)
        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        wk = w.weekly_trend or ("up" if overall >= 65 else ("flat" if overall >= 50 else "down"))

        items.append({
            "ticker": w.ticker,
            "name": w.name or w.ticker,
            "segment": "監視",
            "action": "買い候補" if ai_prob >= 0.6 else "様子見",
            "reasons": w.reason_details or [],
            "ai": {"win_prob": ai_prob, "size_mult": 1.0},
            "theme": {"id": "auto", "label": w.theme_label or "テーマ", "score": theme_score},
            "weekly_trend": wk,
            "overall_score": overall,
            "entry_price_hint": last,
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_price": tp_price, "sl_price": sl_price,
            },
        })
    return items


def _holding_candidates(user) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    qs = Holding.objects.all().order_by("-updated_at")[:8]
    for h in qs:
        last = _price_from_cache_or(h.last_price, h.ticker) or 3000
        tp_pct, sl_pct = 0.1, 0.03
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        items.append({
            "ticker": h.ticker,
            "name": h.name or h.ticker,
            "segment": "中期（保有）",
            "action": "保有継続",
            "reasons": ["保有中銘柄", "中期目線"],
            "ai": {"win_prob": 0.63, "size_mult": 1.0},
            "theme": {"id": "generic", "label": h.sector or "—", "score": 0.55},
            "weekly_trend": "flat",
            "overall_score": 55,
            "entry_price_hint": last,
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_price": tp_price, "sl_price": sl_price,
            },
        })
    return items


def _attach_sizing(items: List[Dict[str, Any]], credit_balance: int, risk_per_trade: float = 0.01) -> None:
    for it in items:
        entry = int(it.get("entry_price_hint") or 3000)
        sl_price = int(it["targets"].get("sl_price") or max(1, int(round(entry * 0.98))))
        stop_value = max(1, entry - sl_price)
        risk_budget = max(1, int(round(credit_balance * risk_per_trade)))
        shares = risk_budget // stop_value if stop_value > 0 else 0
        need_cash = shares * entry if shares > 0 else None

        it["sizing"] = {
            "credit_balance": credit_balance,
            "risk_per_trade": risk_per_trade,
            "position_size_hint": shares if shares > 0 else None,
            "need_cash": need_cash,
        }


# ---------- public entry ----------
def build_board(user, *, use_cache: bool = True) -> Dict[str, Any]:
    """
    /advisor/api/board/ が呼ぶ実データビルダー（キャッシュ対応版）
    優先順: TrendResult → WatchEntry → Holding
    """
    if use_cache:
        cached = _load_board_cache(user)
        if cached is not None:
            return cached

    now = _jst_now()
    credit = _resolve_credit_balance(user)
    risk_per_trade = 0.01

    items = _trend_candidates(user)
    if len(items) < 5:
        items += _watch_candidates(user)
    if len(items) < 5:
        items += _holding_candidates(user)
    items = items[:5]

    _attach_sizing(items, credit_balance=credit, risk_per_trade=risk_per_trade)

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": now.replace(second=0, microsecond=0).isoformat(),
            "model_version": "v0.4-trend-priority",
            "credit_balance": int(credit),
            "live": True,
        },
        "theme": {
            "week": now.strftime("%Y-W%V"),
            "top3": [
                {"id": "trend", "label": "AIトレンド", "score": 0.65},
                {"id": "watch", "label": "監視銘柄", "score": 0.58},
                {"id": "hold", "label": "保有補完", "score": 0.55},
            ],
        },
        "highlights": items,
    }

    if use_cache:
        _save_board_cache(user, data, ttl_minutes=180)

    return data