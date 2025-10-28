# advisor/services/board_source.py
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from django.db.models import Sum, Max
from django.utils.timezone import now as dj_now
from django.contrib.auth import get_user_model

# ポートフォリオ側の実データ
from portfolio.models import Holding
from portfolio.models_cash import BrokerAccount, CashLedger, MarginState
# アドバイザー側（候補や理由テキストを流用）
from advisor.models import WatchEntry

User = get_user_model()
JST = timezone(timedelta(hours=9))

# ---- キャッシュ層（存在しなくても動くように安全に読み込み） -----------------
_HAS_CACHE = False
try:
    from advisor.models_cache import PriceCache, BoardCache  # 追加モデル（任意）
    _HAS_CACHE = True
except Exception:
    PriceCache = None  # type: ignore
    BoardCache = None  # type: ignore
    _HAS_CACHE = False

# ---- トレンド層（存在しなくてもOK） -------------------------------------------
_HAS_TREND = False
try:
    from advisor.models_trend import TrendResult  # 任意：あれば使う
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
    """
    信用口座の MarginState から available_funds を合計。
    無ければ None を返す（キャッシュ台帳フォールバック側で扱う）。
    """
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

    # 各アカウントの最新 as_of を拾って合計
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
    """
    MarginState が無い場合のフォールバック：
    - 信用口座(JPY)の opening_balance + CashLedger 累積（入出金・振替）
    """
    accts = BrokerAccount.objects.filter(account_type="信用", currency="JPY")
    total = 0
    for a in accts:
        bal = _safe_int(a.opening_balance, 0)
        led = CashLedger.objects.filter(account=a).aggregate(s=Sum("amount"))["s"] or 0
        total += int(bal + led)
    return max(0, total)

def _resolve_credit_balance(user) -> int:
    """信用余力の決定ロジック。まず MarginState、無ければ台帳フォールバック。"""
    m = _latest_margin_available_funds(user)
    if m is not None:
        return max(0, int(m))
    return _cash_fallback_credit(user)

# ---- 価格キャッシュの参照（任意） ---------------------------------------------
def _price_from_cache_or(value_hint: Optional[int], ticker: str) -> Optional[int]:
    """
    PriceCache があればそれを優先。無ければ引数の value_hint（entry_price_hint 等）を返す。
    どちらもなければ None。
    """
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
            # liveフラグ & バージョン追記
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
        # キャッシュ保存に失敗してもAPIは正常返却させる
        pass

# ---- トレンド参照（任意） ------------------------------------------------------
def _trend_for(ticker: str) -> Optional[Dict[str, float]]:
    """
    TrendResult の最新 asof を1件返す。無ければ None。
    """
    if not _HAS_TREND or TrendResult is None:
        return None
    row = (
        TrendResult.objects
        .filter(ticker=(ticker or "").upper())
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

# ---- TrendResult からの候補（最優先） ------------------------------------------
def _trend_candidates(user, limit: int = 8) -> List[Dict[str, Any]]:
    """
    TrendResult の “当日（最新日付）データ” からハイライト候補を作る（最優先）。
    - 最新 asof を特定 → その日のレコードを overall_score / slope_annual で上位抽出
    - entry_price_hint は PriceCache を優先
    - win_prob が無ければ slope/confidence から簡易推定（守り気味）
    """
    items: List[Dict[str, Any]] = []
    if not (_HAS_TREND and TrendResult is not None):
        return items

    latest_asof = (
        TrendResult.objects.filter(user=user).aggregate(m=Max("asof"))["m"]
        if user else None
    )
    if not latest_asof:
        return items

    qs = (
        TrendResult.objects
        .filter(user=user, asof=latest_asof)
        .order_by("-overall_score", "-slope_annual")
    )[: max(limit, 8)]

    for tr in qs:
        last = _price_from_cache_or(tr.entry_price_hint, tr.ticker) or tr.entry_price_hint or 3000
        try:
            last = int(last)
        except Exception:
            last = 3000

        # win_prob の安全な決め方（なければ slope/confidence から控えめに推定）
        ai_prob = float(tr.win_prob) if tr.win_prob is not None else max(0.35, min(0.75, 0.5 + 0.3 * float(getattr(tr, "slope_annual", 0.0) or 0.0)))
        conf = float(getattr(tr, "confidence", 0.5) or 0.5)
        # confidence で±5%だけ微調整（過信しない）
        ai_prob = min(0.95, max(0.05, ai_prob + (conf - 0.5) * 0.10))

        theme_score = float(tr.theme_score) if tr.theme_score is not None else 0.55
        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        wk = (tr.weekly_trend or "flat").lower()
        if wk not in ("up", "down", "flat"):
            wk = "up" if overall >= 65 else ("flat" if overall >= 50 else "down")

        # 簡易TP/SL（中期デフォルト）
        tp_pct = 0.10; sl_pct = 0.03
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        items.append({
            "ticker": tr.ticker.upper(),
            "name": tr.name or tr.ticker,
            "segment": "トレンド（最新）",
            "action": "買い候補" if ai_prob >= 0.60 else "様子見",
            "reasons": ["TrendResultベース", f"信頼度{int(conf*100)}%", f"slope≈{round(float(getattr(tr,'slope_annual',0.0) or 0.0)*100,1)}%/yr"],
            "ai": {"win_prob": ai_prob, "size_mult": float(getattr(tr, "size_mult", 1.0) or 1.0)},
            "theme": {"id": "trend", "label": tr.theme_label or "テーマ", "score": theme_score},
            "weekly_trend": wk,
            "overall_score": overall if tr.overall_score is None else int(tr.overall_score),
            "entry_price_hint": last,
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
        })
    return items

# ---------- 監視からの候補 ----------
def _watch_candidates(user) -> List[Dict[str, Any]]:
    """
    WatchEntry から候補を作る（TrendResultで不足分の補完用）。
    """
    items: List[Dict[str, Any]] = []
    qs = (
        WatchEntry.objects
        .filter(status=WatchEntry.STATUS_ACTIVE)
        .order_by("-updated_at")[:12]
    )
    for w in qs:
        # 価格ヒント（PriceCache優先 → entry_price_hint → 3000）
        last = _price_from_cache_or(w.entry_price_hint, w.ticker) or 3000

        # ％の素直な補完
        tp_pct = float(w.tp_pct or 0.06)
        sl_pct = float(w.sl_pct or 0.02)
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        # AI/テーマ
        ai_prob = float(w.ai_win_prob or 0.62)
        theme_score = float(w.theme_score or 0.55)

        # ---- トレンド補強（あれば） ----
        wk = (w.weekly_trend or "").strip().lower()
        tr = _trend_for(w.ticker)
        if tr:
            wk = (tr["weekly_trend"] or wk).lower()
            # confidence を ±5%の範囲で win_prob に微調整（過信しない）
            adjust = max(-0.05, min(0.05, (tr["confidence"] - 0.5) * 0.10))
            ai_prob = min(0.95, max(0.05, ai_prob + adjust))

        if wk not in ("up", "down", "flat"):
            # fallback
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
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
        })
    return items

# ---------- 保有からの候補 ----------
def _holding_candidates(user) -> List[Dict[str, Any]]:
    """
    Holding から候補を作る（板を埋める用の補完）。
    last_price → PriceCache → 3000。
    """
    items: List[Dict[str, Any]] = []
    qs = Holding.objects.filter(user=user).order_by("-updated_at")[:12]
    for h in qs:
        # last_price or PriceCache or 3000
        base_price = None
        if h.last_price is not None:
            try:
                base_price = int(round(float(h.last_price)))
            except Exception:
                base_price = None
        last = _price_from_cache_or(base_price, h.ticker) or 3000

        # 簡易％（中期デフォルト）
        tp_pct = 0.10; sl_pct = 0.03
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        ai_prob = 0.63  # 暫定
        theme_score = 0.55

        # ---- トレンド補強（あれば） ----
        wk = None
        tr = _trend_for(h.ticker)
        if tr:
            wk = (tr["weekly_trend"] or "flat").lower()
            adjust = max(-0.05, min(0.05, (tr["confidence"] - 0.5) * 0.10))
            ai_prob = min(0.95, max(0.05, ai_prob + adjust))

        overall = int(round((ai_prob * 0.7 + theme_score * 0.3) * 100))
        if wk not in ("up", "down", "flat"):
            wk = "up" if overall >= 65 else ("flat" if overall >= 50 else "down")

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
            "targets": {
                "tp": f"目標 +{int(tp_pct*100)}%",
                "sl": f"損切り -{int(sl_pct*100)}%",
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
        })
    return items

# ---------- sizing ----------
def _attach_sizing(items: List[Dict[str, Any]], credit_balance: int, risk_per_trade: float = 0.01) -> None:
    """
    既存スキーマに合わせてサイズ目安と確率ラベルを埋める。
    """
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
    /advisor/api/board/ が呼ぶ実データビルダー（キャッシュ対応版）。
    優先度: TrendResult（当日） → WatchEntry → Holding
    """
    # 1) キャッシュ即返
    if use_cache:
        cached = _load_board_cache(user)
        if cached is not None:
            return cached

    # 2) 実データでビルド
    now = _jst_now()
    credit = _resolve_credit_balance(user)
    risk_per_trade = 0.01

    # 最優先：TrendResult（当日）
    items = _trend_candidates(user, limit=8)

    # 次点：Watch（不足を補う）
    if len(items) < 5:
        seen = {it["ticker"] for it in items}
        more = [it for it in _watch_candidates(user) if it["ticker"] not in seen]
        items += more

    # さらに不足：Holding
    if len(items) < 5:
        seen = {it["ticker"] for it in items}
        more = [it for it in _holding_candidates(user) if it["ticker"] not in seen]
        items += more

    # 上位5件まで
    items = items[:5]

    _attach_sizing(items, credit_balance=credit, risk_per_trade=risk_per_trade)

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": now.replace(second=0, microsecond=0).isoformat(),
            "model_version": "v0.4-trend-first",
            "adherence_week": 0.84,  # 後で learn ダッシュ連携
            "regime": {"trend_prob": 0.55, "range_prob": 0.45, "nikkei": "→", "topix": "→"},
            "scenario": "TrendResult最優先で今日の候補を生成（当日）",
            "pairing": {"id": 2, "label": "順張り・短中期"},
            "self_mirror": {"recent_drift": "—"},
            "credit_balance": int(credit),
            "live": True,   # 実データ経路
        },
        "theme": {
            "week": now.strftime("%Y-W%V"),
            "top3": [
                {"id": "trend",   "label": "トレンド優先", "score": 0.60},
                {"id": "generic", "label": "監視テーマ",   "score": 0.56},
                {"id": "補完",     "label": "保有補完",     "score": 0.52},
            ],
        },
        "highlights": items,
    }

    # 3) キャッシュ保存（任意）
    if use_cache:
        _save_board_cache(user, data, ttl_minutes=180)

    return data