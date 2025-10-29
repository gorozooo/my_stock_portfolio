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

# ---- キャッシュ層 --------------------------------------------------------------
_HAS_CACHE = False
try:
    from advisor.models_cache import PriceCache, BoardCache
    _HAS_CACHE = True
except Exception:
    PriceCache = None  # type: ignore
    BoardCache = None  # type: ignore
    _HAS_CACHE = False

# ---- トレンド層 ----------------------------------------------------------------
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

def _display_name(user, ticker: str, trend_name: Optional[str] = None) -> str:
    """表示名: TrendResult.name → Holding.name → WatchEntry.name → ticker"""
    if trend_name and str(trend_name).strip():
        return str(trend_name).strip()
    t = (ticker or "").upper()
    h = Holding.objects.filter(user=user, ticker=t).only("name").first()
    if h and (h.name or "").strip():
        return h.name.strip()
    w = WatchEntry.objects.filter(user=user, ticker=t).only("name").first()
    if w and (w.name or "").strip():
        return w.name.strip()
    return t


# ---- BoardCache ---------------------------------------------------------------
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


# ---- TrendResult 取り出し（DB依存を吸収） --------------------------------------
def _trend_rows_latest_per_ticker(user) -> List[Any]:
    """
    ユーザーの各 ticker について「最新 asof の1件」を返す。
    Postgres の distinct('ticker') が無い環境でも動くようフォールバック。
    """
    if not (_HAS_TREND and TrendResult is not None):
        return []
    try:
        # Postgres: 速い
        qs = (
            TrendResult.objects
            .filter(user=user)
            .order_by("ticker", "-asof")
            .distinct("ticker")
        )
        return list(qs)
    except Exception:
        # フォールバック: Python側で最新だけ抽出
        rows = (
            TrendResult.objects
            .filter(user=user)
            .order_by("-asof", "-updated_at")
            .only("ticker", "name", "asof", "overall_score", "entry_price_hint",
                  "weekly_trend", "confidence", "slope_annual", "theme_label", "theme_score", "size_mult")
        )
        seen = set()
        latest = []
        for r in rows:
            t = r.ticker.upper()
            if t in seen:
                continue
            seen.add(t)
            latest.append(r)
        return latest


def _trend_candidates(user, limit=12) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    rows = _trend_rows_latest_per_ticker(user)

    # スコア降順でソート
    rows = sorted(
        rows,
        key=lambda r: (int(r.overall_score or 0), float(r.confidence or 0.0)),
        reverse=True,
    )[:limit]

    for r in rows:
        # ✅ ここを修正：キャッシュ最優先 → ヒント → close
        last = _price_from_cache_or(r.entry_price_hint, r.ticker) or r.close_price or 3000

        tp_pct = 0.10; sl_pct = 0.03
        tp_price = int(round(int(last) * (1 + tp_pct)))
        sl_price = int(round(int(last) * (1 - sl_pct)))

        # overall_score が無い場合の暫定勝率（従来ロジック踏襲）
        win_prob = float(r.overall_score or 60) / 100.0

        items.append({
            "ticker": r.ticker.upper(),
            "name": _display_name(user, r.ticker, r.name),
            "segment": "トレンド（最新）",
            "action": "買い候補",
            "reasons": [
                "TrendResultベース",
                f"信頼度{int(round(float(r.confidence or 0.5)*100))}%",
                f"slope≈{round(float(r.slope_annual or 0.0)*100,1)}%/yr",
            ],
            "ai": {"win_prob": win_prob, "size_mult": float(r.size_mult or 1.0)},
            "theme": {"id": "trend", "label": (r.theme_label or "trend"), "score": float(r.theme_score or 0.55)},
            "weekly_trend": (r.weekly_trend or "flat"),
            "overall_score": int(r.overall_score or max(35, int((float(r.confidence or 0.5))*100))),
            "entry_price_hint": int(last),
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
        })
    return items


# ---- watch/holding は不足時のみ補完 --------------------------------------------
def _watch_candidates(user) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    qs = (
        WatchEntry.objects
        .filter(user=user, status=WatchEntry.STATUS_ACTIVE)  # ✅ user を追加
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
        if wk not in ("up", "down", "flat"):
            overall_tmp = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
            wk = "up" if overall_tmp >= 65 else ("flat" if overall_tmp >= 50 else "down")
        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        items.append({
            "ticker": w.ticker,
            "name": _display_name(user, w.ticker, w.name),
            "segment": "監視",
            "action": "買い候補" if ai_prob >= 0.6 else "様子見",
            "reasons": w.reason_details or ((w.reason_summary or "").split("/") if w.reason_summary else []),
            "ai": {"win_prob": ai_prob, "size_mult": 1.0},
            "theme": {"id": "auto", "label": (w.theme_label or "テーマ"), "score": theme_score},
            "weekly_trend": wk,
            "overall_score": overall,
            "entry_price_hint": last,
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
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
        ai_prob = 0.63; theme_score = 0.55
        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        wk = "up" if overall >= 65 else ("flat" if overall >= 50 else "down")
        items.append({
            "ticker": h.ticker.upper(),
            "name": _display_name(user, h.ticker, h.name),
            "segment": "保有",
            "action": "買い候補（簡易）",
            "reasons": ["既存保有/監視から抽出", "価格は最新値ベース", "暫定パラメータ"],
            "ai": {"win_prob": ai_prob, "size_mult": 1.0},
            "theme": {"id": "generic", "label": h.sector or "—", "score": theme_score},
            "weekly_trend": wk,
            "overall_score": overall,
            "entry_price_hint": last,
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
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
        it["sizing"] = {
            "credit_balance": credit_balance,
            "risk_per_trade": risk_per_trade,
            "position_size_hint": shares if shares > 0 else None,
            "need_cash": need_cash,
        }
        it["ai"] = {**it.get("ai", {}), "tp_prob": tp_prob, "sl_prob": sl_prob}


# ---------- public entry ----------
def build_board(user, *, use_cache: bool = True) -> Dict[str, Any]:
    """
    優先度: TrendResult（5件） → 不足分をWatchEntry/ Holdingで補完
    """
    if use_cache:
        cached = _load_board_cache(user)
        if cached is not None:
            return cached

    now = _jst_now()
    credit = _resolve_credit_balance(user)
    risk_per_trade = 0.01

    items: List[Dict[str, Any]] = []
    items += _trend_candidates(user, limit=12)
    if len(items) < 5:
        items += _watch_candidates(user)
    if len(items) < 5:
        items += _holding_candidates(user)
    # 最終的に5件に整形
    items = items[:5]

    _attach_sizing(items, credit_balance=credit, risk_per_trade=risk_per_trade)

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": now.replace(second=0, microsecond=0).isoformat(),
            "model_version": "v0.5-trend-first",
            "adherence_week": 0.84,
            "regime": {"trend_prob": 0.55, "range_prob": 0.45, "nikkei": "→", "topix": "→"},
            "scenario": "TrendResult最優先。足りない分のみ監視/保有で補完。",
            "pairing": {"id": 2, "label": "順張り・短中期"},
            "self_mirror": {"recent_drift": "—"},
            "credit_balance": int(credit),
            "live": True,
            "source_breakdown": {
                "trend": sum(1 for x in items if x.get("segment") == "トレンド（最新）"),
                "watch": sum(1 for x in items if x.get("segment") == "監視"),
                "holding": sum(1 for x in items if x.get("segment") == "保有"),
            },
        },
        "theme": {
            "week": now.strftime("%Y-W%V"),
            "top3": [
                {"id": "trend",   "label": "トレンド強度", "score": 0.60},
                {"id": "generic", "label": "監視テーマ",   "score": 0.56},
                {"id": "generic2","label": "セクター",     "score": 0.55},
            ],
        },
        "highlights": items,
    }

    if use_cache:
        _save_board_cache(user, data, ttl_minutes=180)

    return data