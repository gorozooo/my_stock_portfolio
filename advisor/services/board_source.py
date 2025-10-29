from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from django.db.models import Max
from django.utils.timezone import now as dj_now

from advisor.models_trend import TrendResult
from advisor.models_cache import PriceCache, BoardCache  # ある前提（無ければtry/except可）
from portfolio.models_cash import MarginState, BrokerAccount, CashLedger
from portfolio.models import Holding
from advisor.models import WatchEntry
from .policy_loader import load_active_policies

JST = timezone(timedelta(hours=9))

# ---------- helpers ----------
def _now_jst(): return dj_now().astimezone(JST)

def _price(ticker: str, fallback: Optional[int]) -> int:
    pc = PriceCache.objects.filter(ticker=ticker.upper()).first()
    if pc and pc.last_price is not None:
        return int(pc.last_price)
    return int(fallback or 3000)

def _credit_balance(user) -> int:
    qs = MarginState.objects.filter(
        account__in=BrokerAccount.objects.filter(account_type="信用", currency="JPY")
    )
    if not qs.exists():
        # 現金台帳の合計で代用
        total = 0
        for a in BrokerAccount.objects.filter(account_type="信用", currency="JPY"):
            led = CashLedger.objects.filter(account=a).aggregate_sum("amount") if hasattr(CashLedger.objects, "aggregate_sum") else None
            if led and hasattr(led, "get"): total += int(led.get("amount__sum") or 0)
        return max(0, total)
    latest = qs.values("account_id").annotate(m=Max("as_of"))
    m = 0
    for row in latest:
        st = qs.filter(account_id=row["account_id"], as_of=row["m"]).first()
        if st: m += int(st.available_funds or 0)
    return max(0, m)

def _latest_trends(user) -> List[TrendResult]:
    """銘柄ごとに直近asofの1件を返す（DBに依らないフォールバック）"""
    rows = TrendResult.objects.filter(user=user).order_by("-asof", "-updated_at")
    seen, out = set(), []
    for r in rows:
        t = r.ticker.upper()
        if t in seen: continue
        seen.add(t); out.append(r)
    return out

def _passes(tr: TrendResult, pol: Dict[str, Any]) -> bool:
    r = pol["rules"]
    if tr.overall_score is not None and int(tr.overall_score) < int(r["min_overall"]): return False
    if tr.theme_score   is not None and float(tr.theme_score) < float(r["min_theme"]): return False
    if tr.weekly_trend and tr.weekly_trend not in r["allow_weekly"]: return False
    if r.get("min_slope_yr") is not None:
        s = float(tr.slope_annual or 0.0)
        if s < float(r["min_slope_yr"]): return False
    return True

def _card_from(tr: TrendResult, pol: Dict[str, Any], credit: int) -> Dict[str, Any]:
    tp_pct = float(pol["targets"]["tp_pct"]); sl_pct = float(pol["targets"]["sl_pct"])
    entry  = _price(tr.ticker, tr.entry_price_hint or tr.close_price)
    tp_price = int(round(entry * (1+tp_pct)))
    sl_price = int(round(entry * (1-sl_pct)))
    risk_pct = float(pol["size"]["risk_pct"])
    # position size
    stop_value = max(1, entry - sl_price)
    risk_budget = max(1, int(round(credit * risk_pct)))
    shares = risk_budget // stop_value if stop_value>0 else 0
    need_cash = shares * entry if shares>0 else None
    # 表示名は Trend→Holding→Watch→ticker
    name = tr.name or Holding.objects.filter(user=tr.user, ticker=tr.ticker.upper()).values_list("name", flat=True).first() \
           or WatchEntry.objects.filter(user=tr.user, ticker=tr.ticker.upper()).values_list("name", flat=True).first() \
           or tr.ticker.upper()
    win_prob = float(tr.overall_score or 60) / 100.0
    return {
        "policy_id": pol["id"],
        "ticker": tr.ticker.upper(),
        "name": name,
        "segment": pol["labels"]["segment"],
        "action":  pol["labels"]["action"],
        "reasons": [
            "TrendResultベース",
            f"信頼度{int(round(float(tr.confidence or 0.5)*100))}%",
            f"slope≈{round(float(tr.slope_annual or 0.0)*100,1)}%/yr",
        ],
        "ai": {"win_prob": win_prob, "size_mult": float(tr.size_mult or 1.0)},
        "theme": {"id": "trend", "label": (tr.theme_label or "trend"), "score": float(tr.theme_score or 0.55)},
        "weekly_trend": (tr.weekly_trend or "flat"),
        "overall_score": int(tr.overall_score or 60),
        "entry_price_hint": entry,
        "targets": {
            "tp": f"目標 +{int(tp_pct*100)}%",
            "sl": f"損切り -{int(sl_pct*100)}%",
            "tp_pct": tp_pct, "sl_pct": sl_pct,
            "tp_price": tp_price, "sl_price": sl_price,
        },
        "sizing": {
            "credit_balance": credit,
            "risk_per_trade": risk_pct,
            "position_size_hint": (shares if shares>0 else None),
            "need_cash": need_cash,
        },
    }

def build_board(user, *, use_cache: bool=True) -> Dict[str, Any]:
    # 1) キャッシュ
    if use_cache:
        bc = BoardCache.objects.filter(user=user).first() or BoardCache.objects.filter(user__isnull=True).first()
        if bc and bc.is_fresh:
            payload = dict(bc.payload); payload.setdefault("meta", {})["live"]=True
            return payload

    # 2) ポリシー取得
    policies = load_active_policies()
    credit = _credit_balance(user)
    now = _now_jst()

    # 3) 最新TrendResultを全銘柄で取得→各ポリシーでフィルタ→重畳スコアで並べ替え
    rows = _latest_trends(user)
    cards: List[Dict[str, Any]] = []
    for pol in policies:
        cand = [tr for tr in rows if _passes(tr, pol)]
        # policy内の優先順位：overall_score desc, confidence desc
        cand.sort(key=lambda r: (int(r.overall_score or 0), float(r.confidence or 0.0)), reverse=True)
        cand = cand[: int(pol.get("limit", 20))]
        for tr in cand:
            cards.append((_card_from(tr, pol, credit), int(pol.get("priority",50))))

    # 4) 全体の並べ替え： policy.priorityを重みとした総合点
    cards.sort(key=lambda item: (item[1], int(item[0].get("overall_score",0))), reverse=True)
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
            "source_breakdown": {},
        },
        "theme": {
            "week": now.strftime("%Y-W%V"),
            "top3": [
                {"id":"trend","label":"トレンド強度","score":0.60},
                {"id":"generic","label":"監視テーマ","score":0.56},
                {"id":"generic2","label":"セクター","score":0.55},
            ],
        },
        "highlights": highlights,
    }
    # breakdown
    data["meta"]["source_breakdown"] = {
        "policies": {p["id"]: sum(1 for h in highlights if h.get("policy_id")==p["id"]) for p in policies}
    }
    # 5) キャッシュ保存
    if use_cache:
        BoardCache.objects.create(user=user, payload=data, generated_at=now, ttl_minutes=180, note="policy")

    return data