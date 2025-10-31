from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timezone, timedelta
import os, json

from django.db.models import Max
from django.utils.timezone import now as dj_now
from django.conf import settings
from django.contrib.auth import get_user_model

from advisor.models_trend import TrendResult
from portfolio.models_cash import MarginState, BrokerAccount, CashLedger
from portfolio.models import Holding
from advisor.models import WatchEntry
from advisor.services.policy_rules import compute_exit_targets

try:
    from advisor.models_cache import PriceCache, BoardCache
except Exception:
    PriceCache = None  # type: ignore
    BoardCache = None  # type: ignore

from .policy_loader import load_active_policies

User = get_user_model()
JST = timezone(timedelta(hours=9))

# ==============================
# JPX銘柄マップ（tse_list.json）
# ・value が "ソニーG" でも {"name":"ソニーG","sector":"電機","market":"プライム"} でもOK
# ==============================
def _load_tse_map() -> Dict[str, Union[str, Dict[str, Any]]]:
    base_dir = getattr(settings, "BASE_DIR", os.getcwd())
    path = os.path.join(base_dir, "data", "tse_list.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = json.load(f)
        if isinstance(m, dict):
            return m
    except Exception:
        pass
    return {}

_TSE_MAP: Dict[str, Union[str, Dict[str, Any]]] = _load_tse_map()

def _tse_lookup(ticker: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    return: (jp_name, sector, market) いずれも無ければ None
    """
    t = str(ticker).upper().strip()
    if t.endswith(".T"):
        t = t[:-2]
    v = _TSE_MAP.get(t)
    if v is None:
        return None, None, None
    if isinstance(v, str):
        return v.strip() or None, None, None
    # dict 期待: {"name": "...", "sector": "...", "market": "..."} など
    name = str(v.get("name") or "").strip() or None
    sector = (str(v.get("sector")).strip() or None) if "sector" in v else None
    market = (str(v.get("market")).strip() or None) if "market" in v else None
    return name, sector, market

def _display_ticker(t: str) -> str:
    t = (t or "").strip().upper()
    if t.isdigit() and 4 <= len(t) <= 5:
        return f"{t}.T"
    return t

# _card_from 内の "ticker" だけ置き換え
"ticker": _display_ticker(tr.ticker),

# ----------------
# 共通ヘルパー群
# ----------------
def _now_jst() -> datetime:
    return dj_now().astimezone(JST)

def _price_from_cache_or(ticker: str, fallback: Optional[int]) -> int:
    if PriceCache is not None:
        pc = PriceCache.objects.filter(ticker=ticker.upper()).first()
        if pc and pc.last_price is not None:
            try:
                return int(pc.last_price)
            except Exception:
                pass
    return int(fallback or 3000)

def _credit_balance(user) -> int:
    qs = MarginState.objects.filter(
        account__in=BrokerAccount.objects.filter(account_type="信用", currency="JPY")
    )
    if not qs.exists():
        total = 0
        for a in BrokerAccount.objects.filter(account_type="信用", currency="JPY"):
            try:
                from django.db.models import Sum
                total += int(CashLedger.objects.filter(account=a).aggregate(s=Sum("amount"))["s"] or 0)
            except Exception:
                pass
        return max(0, total)

    latest = qs.values("account_id").annotate(m=Max("as_of"))
    s = 0
    for row in latest:
        st = qs.filter(account_id=row["account_id"], as_of=row["m"]).first()
        if st and st.available_funds is not None:
            s += int(st.available_funds)
    return max(0, s)

def _latest_trends(user) -> List[TrendResult]:
    rows = TrendResult.objects.filter(user=user).order_by("-asof", "-updated_at")
    seen, out = set(), []
    for r in rows:
        t = r.ticker.upper()
        if t in seen: 
            continue
        seen.add(t)
        out.append(r)
    return out

def _passes_policy(tr: TrendResult, pol: Dict[str, Any]) -> bool:
    r = pol["rules"]
    if tr.overall_score is not None and int(tr.overall_score) < int(r["min_overall"]):
        return False
    if tr.theme_score is not None and float(tr.theme_score) < float(r["min_theme"]):
        return False
    if tr.weekly_trend and tr.weekly_trend not in r["allow_weekly"]:
        return False
    if r.get("min_slope_yr") is not None:
        if float(tr.slope_annual or 0.0) < float(r["min_slope_yr"]):
            return False
    return True

def _exit_targets_from_policy(pol: Dict[str, Any], tr: TrendResult, entry: Optional[int]) -> Dict[str, Any]:
    """
    ポリシー(rule_json)の exits 数値ルールからTP/SL等を決める。
    TrendResult.notes['atr14'] があれば yfinance を叩かずに使う。
    """
    if not pol:
        return {"tp_pct": None, "sl_pct": None, "tp_price": None, "sl_price": None, "trail_atr_mult": None, "time_exit_due": False}

    rules = {
        "targets": pol.get("targets", {}),
        "exits": pol.get("exits", {}),
    }
    atr_hint = None
    try:
        n = tr.notes or {}
        atr_hint = float(n.get("atr14")) if n.get("atr14") is not None else None
    except Exception:
        atr_hint = None

    xt = compute_exit_targets(
        policy=rules,
        ticker=tr.ticker.upper(),
        entry_price=entry,
        days_held=None,
        atr14_hint=atr_hint,  # ★ キャッシュをヒントに
    )
    return {
        "tp_pct": pol.get("targets", {}).get("tp_pct"),
        "sl_pct": pol.get("targets", {}).get("sl_pct"),
        "tp_price": xt.tp_price,
        "sl_price": xt.sl_price,
        "trail_atr_mult": xt.trail_atr_mult,
        "time_exit_due": xt.time_exit_due,
        "_notes": xt.notes,
    }

# ================
# 名称の決定ロジック
# ================
def _resolve_display(user, tr: TrendResult) -> Tuple[str, Optional[str], Optional[str]]:
    """
    表示名は必ず "文字列" を返す。sector/market は追加メタ。
    1) JPX マップ（最優先）
    2) TrendResult.name
    3) Holding/Watch の name
    4) ティッカー
    さらに（攻め）: TrendResult.name が未設定/英名なら JPX名で静かに補完
    """
    jp_name, sector, market = _tse_lookup(tr.ticker)
    if not jp_name:
        # フォールバック: TrendResult / Holding / Watch
        if (tr.name or "").strip():
            name = tr.name.strip()
        else:
            t = tr.ticker.upper()
            h = Holding.objects.filter(user=user, ticker=t).only("name").first()
            w = None if h and (h.name or "").strip() else WatchEntry.objects.filter(user=user, ticker=t).only("name").first()
            name = (h.name if h and (h.name or "").strip() else (w.name if w and (w.name or "").strip() else t))
        return str(name), sector, market

    # --- 攻めの改善：DBへ和名を自動補完（ノイズ少なめに） ---
    try:
        if (not tr.name) or any(ch.isascii() for ch in str(tr.name)):  # 英字ベースなら置換して良いケースが多い
            if tr.name != jp_name:
                tr.name = jp_name
                tr.save(update_fields=["name"])
    except Exception:
        pass

    return jp_name, sector, market

def _card_from(tr: TrendResult, pol: Dict[str, Any], credit: int) -> Dict[str, Any]:
    entry = _price_from_cache_or(tr.ticker, tr.entry_price_hint or tr.close_price)

    exit_cfg = _exit_targets_from_policy(pol, tr, entry)
    tp_price = exit_cfg["tp_price"]
    sl_price = exit_cfg["sl_price"]
    tp_pct = exit_cfg["tp_pct"]
    sl_pct = exit_cfg["sl_pct"]
    time_due = bool(exit_cfg.get("time_exit_due", False))
    trail_mult = exit_cfg.get("trail_atr_mult")

    risk_pct = float(pol["size"]["risk_pct"])
    stop_value = max(1, entry - (sl_price if sl_price is not None else int(round(entry * (1 - float(sl_pct or 0))))))
    risk_budget = max(1, int(round(credit * risk_pct)))
    shares = risk_budget // stop_value if stop_value > 0 else 0
    need_cash = shares * entry if shares > 0 else None

    name, sector, market = _resolve_display(tr.user, tr)
    win_prob = float(tr.overall_score or 60) / 100.0

    # ★ 時間切れを行動ラベルへ反映（“攻め”）
    base_action = pol["labels"]["action"]
    action = ("縮小/撤退候補（時間未達）" if time_due else base_action)

    card = {
        "policy_id": pol["id"],
        "ticker": tr.ticker.upper(),
        "name": str(name),
        "segment": pol["labels"]["segment"],
        "action":  action,
        "reasons": [
            "Policy数値ルール適用",
            f"信頼度{int(round(float(tr.confidence or 0.5)*100))}%",
            f"slope≈{round(float(tr.slope_annual or 0.0)*100,1)}%/yr",
        ],
        "ai": {"win_prob": win_prob, "size_mult": float(tr.size_mult or 1.0)},
        "theme": {"id": "trend", "label": (tr.theme_label or "trend"), "score": float(tr.theme_score or 0.55)},
        "weekly_trend": (tr.weekly_trend or "flat"),
        "overall_score": int(tr.overall_score or 60),
        "entry_price_hint": entry,
        "targets": {
            "tp": f"目標 {f'+{int(tp_pct*100)}%' if tp_pct is not None else '+?%'}" +
                  (f" → {tp_price:,}円" if tp_price is not None else ""),
            "sl": f"損切り {f'-{int(sl_pct*100)}%' if sl_pct is not None else '-?%'}" +
                  (f" → {sl_price:,}円" if sl_price is not None else ""),
            "tp_pct": tp_pct, "sl_pct": sl_pct,
            "tp_price": tp_price, "sl_price": sl_price,
            "trail_atr_mult": trail_mult,     # ★ UIで薄く表示
            "time_exit_due": time_due,        # ★ バッジ表示に使用
            "_exit_notes": exit_cfg.get("_notes", {}),
        },
        "sizing": {
            "credit_balance": credit,
            "risk_per_trade": risk_pct,
            "position_size_hint": (shares if shares > 0 else None),
            "need_cash": need_cash,
        },
    }
    if sector or market:
        card["meta"] = {k:v for k,v in (("sector",sector),("market",market)) if v}
    return card
    
def _apply_name_normalization(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    キャッシュ読取時や返却直前に、必ず和名へ正規化し、nameをstrに統一。
    ついでに sector / market を meta に格納（存在する場合）。
    """
    hs = payload.get("highlights") or []
    for h in hs:
        t = str(h.get("ticker") or "").upper()
        jp_name, sector, market = _tse_lookup(t)
        # name 決定（JPX最優先 → 既存値 → ティッカー）
        fallback = h.get("name")
        if isinstance(fallback, dict):
            fallback = fallback.get("name") or ""
        name = jp_name or (fallback if fallback is not None else t)
        h["name"] = str(name)

        # 付随メタ
        meta = dict(h.get("meta") or {})
        if jp_name:  meta.setdefault("jpx_name", jp_name)
        if sector:   meta["sector"] = sector
        if market:   meta["market"] = market
        if meta:     h["meta"] = meta
    return payload

# =====================
# 公開エントリポイント
# =====================
def build_board(user, *, use_cache: bool = True) -> Dict[str, Any]:
    # 1) キャッシュ
    if use_cache and BoardCache is not None:
        bc = BoardCache.objects.filter(user=user).first() or BoardCache.objects.filter(user__isnull=True).first()
        if bc and bc.is_fresh:
            payload = dict(bc.payload)
            payload.setdefault("meta", {})["live"] = True
            return _apply_name_normalization(payload)

    # 2) ポリシー
    policies = load_active_policies()
    credit = _credit_balance(user)
    now = _now_jst()

    # 3) Trend → policy
    rows = _latest_trends(user)
    cards: List[Dict[str, Any]] = []
    for pol in policies:
        cand = [tr for tr in rows if _passes_policy(tr, pol)]
        cand.sort(key=lambda r: (int(r.overall_score or 0), float(r.confidence or 0.0)), reverse=True)
        cand = cand[: int(pol.get("limit", 20))]
        for tr in cand:
            cards.append((_card_from(tr, pol, credit), int(pol.get("priority", 50))))

    cards.sort(key=lambda item: (item[1], int(item[0].get("overall_score", 0))), reverse=True)
    highlights = [c[0] for c in cards][:5]

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": now.replace(second=0, microsecond=0).isoformat(),
            "model_version": "v0.6-trend-first+policy",
            "adherence_week": 0.84,
            "regime": {"trend_prob": 0.60, "range_prob": 0.40, "nikkei": "→", "topix": "→"},
            "scenario": "ポリシー優先のスクリーニング（全銘柄）",
            "pairing": {"id": 2, "label": "順張り・短中期＆NISA"},
            "self_mirror": {"recent_drift": "—"},
            "credit_balance": int(credit),
            "live": True,
            "source_breakdown": {"policies": {p["id"]: sum(1 for h in highlights if h.get("policy_id") == p["id"]) for p in policies}},
        },
        "theme": {
            "week": now.strftime("%Y-W%V"),
            "top3": [
                {"id": "trend", "label": "トレンド強度", "score": 0.60},
                {"id": "generic", "label": "監視テーマ", "score": 0.56},
                {"id": "generic2", "label": "セクター", "score": 0.55},
            ],
        },
        "highlights": highlights,
    }

    data = _apply_name_normalization(data)

    # 4) キャッシュ保存
    if use_cache and BoardCache is not None:
        try:
            BoardCache.objects.create(user=user, payload=data, generated_at=now, ttl_minutes=180, note="policy")
        except Exception:
            pass

    return data