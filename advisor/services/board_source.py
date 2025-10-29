# advisor/services/board_source.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Iterable, Set

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

# ---- キャッシュ層（任意） ------------------------------------------------------
_HAS_CACHE = False
try:
    from advisor.models_cache import PriceCache, BoardCache
    _HAS_CACHE = True
except Exception:
    PriceCache = None  # type: ignore
    BoardCache = None  # type: ignore
    _HAS_CACHE = False

# ---- トレンド層（任意） --------------------------------------------------------
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

def _normalize_ticker(t: str) -> str:
    return (t or "").strip().upper()

def _ticker_variants(t: str) -> List[str]:
    """4755, 4755.T 双方向対応（.T付き/なしを両方試す）。"""
    t = _normalize_ticker(t)
    s: List[str] = [t]
    if t.endswith(".T"):
        s.append(t[:-2])           # 4755.T -> 4755
    else:
        s.append(f"{t}.T")         # 4755 -> 4755.T
    # 重複排除、順序維持
    seen: Set[str] = set()
    out: List[str] = []
    for x in s:
        if x not in seen:
            out.append(x); seen.add(x)
    return out

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
        pc = PriceCache.objects.filter(ticker=_normalize_ticker(ticker)).first()
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

# ---- BoardCache（任意） --------------------------------------------------------
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

# ---- 名前解決（重要：TrendResultのnameを最優先） -------------------------------
def _resolve_display_name(user, ticker: str, trend_name: Optional[str] = None) -> str:
    """
    優先順位:
      1) TrendResult.name（引数で渡された最新の値）
      2) Holding.name（.T付き/無しの両対応）
      3) WatchEntry.name（同上）
      4) 最後はティッカーそのまま
    """
    if (trend_name or "").strip():
        return trend_name.strip()

    variants = _ticker_variants(ticker)
    h = Holding.objects.filter(user=user, ticker__in=variants).only("name").first()
    if h and (h.name or "").strip():
        return h.name.strip()

    w = WatchEntry.objects.filter(user=user, ticker__in=variants).only("name").first()
    if w and (w.name or "").strip():
        return w.name.strip()

    return _normalize_ticker(ticker)

# ---- トレンド1件取得（簡易） ---------------------------------------------------
def _trend_for(ticker: str) -> Optional[Dict[str, float]]:
    if not _HAS_TREND or TrendResult is None:
        return None
    row = (
        TrendResult.objects
        .filter(ticker=_normalize_ticker(ticker).replace(".T", ""))
        .order_by("-asof")
        .first()
    )
    if not row:
        return None
    return {
        "weekly_trend": (row.weekly_trend or "flat"),
        "confidence": float(getattr(row, "confidence", 0.0) or 0.0),
        "slope_annual": float(getattr(row, "slope_annual", 0.0) or 0.0),
    }

# ---------- 候補ビルド ----------------------------------------------------------
def _trend_first_candidates(user) -> List[Dict[str, Any]]:
    """
    TrendResult を最優先でカード化。asof新しい順→overall_score高い順のトップを採用。
    同一ティッカー重複は除外。
    """
    items: List[Dict[str, Any]] = []
    if not (_HAS_TREND and TrendResult is not None):
        return items

    # ユーザーの最新スナップショットから最大12銘柄
    qs = (
        TrendResult.objects
        .filter(user=user)
        .order_by("-asof", "-overall_score", "-updated_at")
    )

    seen: Set[str] = set()
    for row in qs:
        tkr_core = _normalize_ticker(row.ticker).replace(".T", "")
        if tkr_core in seen:
            continue
        seen.add(tkr_core)

        # 価格の決定：PriceCache → row.entry_price_hint → Holding.last_price → 3000
        last = (
            _price_from_cache_or(row.entry_price_hint, tkr_core)
            or _price_from_cache_or(row.entry_price_hint, f"{tkr_core}.T")
        )
        if last is None:
            h = Holding.objects.filter(user=user, ticker__in=_ticker_variants(tkr_core)).only("last_price").first()
            if h and h.last_price is not None:
                try:
                    last = int(round(float(h.last_price)))
                except Exception:
                    last = None
        if last is None:
            last = 3000

        # ％・TP/SL
        ai_prob = float(row.win_prob or 0.60)
        theme_score = float(row.theme_score or 0.55)
        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        tp_pct = 0.10; sl_pct = 0.03
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        # 表示名（今回の肝：TrendResult.nameを最優先）
        disp_name = _resolve_display_name(user, tkr_core, trend_name=row.name)

        items.append({
            "ticker": tkr_core,  # 表示はnameで出すので core で統一
            "name": disp_name,
            "segment": "TrendResultベース",
            "action": "買い候補",
            "reasons": [
                "TrendResultベース",
                f"勝率{int((row.win_prob or 0.6)*100)}%",
                f"slope≈{round(float(getattr(row, 'slope_annual', 0.0))*100, 1)}%/yr",
            ],
            "ai": {"win_prob": ai_prob, "size_mult": float(getattr(row, "size_mult", 1.0) or 1.0)},
            "theme": {"id": "trend", "label": (row.theme_label or ""), "score": theme_score},
            "weekly_trend": (row.weekly_trend or "flat"),
            "overall_score": overall,
            "entry_price_hint": int(last),
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
        })
        if len(items) >= 12:
            break

    return items

def _watch_candidates(user) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    qs = (
        WatchEntry.objects
        .filter(status=WatchEntry.STATUS_ACTIVE, user=user)
        .order_by("-updated_at")[:12]
    )
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
        # ★ ここも resolve_display_name を通す
        disp_name = _resolve_display_name(user, w.ticker, trend_name=None)

        items.append({
            "ticker": _normalize_ticker(w.ticker),
            "name": disp_name,
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

        wk = None
        tr = _trend_for(h.ticker)
        if tr:
            wk = (tr["weekly_trend"] or "flat").lower()
            adjust = max(-0.05, min(0.05, (tr["confidence"] - 0.5) * 0.10))
            ai_prob = min(0.95, max(0.05, ai_prob + adjust))

        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        if wk not in ("up", "down", "flat"):
            wk = "up" if overall >= 65 else ("flat" if overall >= 50 else "down")

        disp_name = _resolve_display_name(user, h.ticker, trend_name=None)

        items.append({
            "ticker": _normalize_ticker(h.ticker),
            "name": disp_name,
            "segment": "中期（20〜45日）",
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
    TrendResult最優先で highlights を構成。足りない分は WatchEntry / Holding で補完。
    返却スキーマは既存互換。
    """
    # 1) キャッシュ
    if use_cache:
        cached = _load_board_cache(user)
        if cached is not None:
            return cached

    # 2) 実データで生成
    now = _jst_now()
    credit = _resolve_credit_balance(user)
    risk_per_trade = 0.01

    items: List[Dict[str, Any]] = []
    # a) TrendResult最優先
    items += _trend_first_candidates(user)
    # b) 足りない分は監視
    if len(items) < 5:
        items += _watch_candidates(user)
    # c) まだ足りなければ保有で補完
    if len(items) < 5:
        items += _holding_candidates(user)

    # 5件に整形（重複ティッカー除去）
    uniq: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for it in items:
        key = _normalize_ticker(it.get("ticker", "")).replace(".T", "")
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
        if len(uniq) >= 5:
            break

    _attach_sizing(uniq, credit_balance=credit, risk_per_trade=risk_per_trade)

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": now.replace(second=0, microsecond=0).isoformat(),
            "model_version": "v0.4-trend-first+cached",
            "adherence_week": 0.84,
            "regime": {"trend_prob": 0.55, "range_prob": 0.45, "nikkei": "→", "topix": "→"},
            "scenario": "TrendResult最優先で今日の候補を生成",
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
                {"id": "generic2","label": "セクター",   "score": 0.52},
            ],
        },
        "highlights": uniq,
    }

    # 3) キャッシュ保存
    if use_cache:
        _save_board_cache(user, data, ttl_minutes=180)

    return data