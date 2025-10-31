# advisor/services/board_source.py
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timezone, timedelta
import os, json

from django.db.models import Max, Sum
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
# JPX銘柄マップ（data/tse_list.json）
# value は "ソニーG" でも {"name":"ソニーG","sector":"電気機器","market":"プライム"} でもOK
# ==============================
def _load_tse_map() -> Dict[str, Union[str, Dict[str, Any]]]:
    base_dir = getattr(settings, "BASE_DIR", os.getcwd())
    path = os.path.join(base_dir, "data", "tse_list.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}

_TSE_MAP: Dict[str, Union[str, Dict[str, Any]]] = _load_tse_map()


# ----------------
# 文字列正規化
# ----------------
def _norm_key(t: str) -> str:
    """重複排除用キー：大文字＋末尾'.T'を剥がす"""
    u = (t or "").strip().upper()
    return u[:-2] if u.endswith(".T") else u

def _display_ticker(t: str) -> str:
    """UI表示は必ず .T 付き"""
    u = (t or "").strip().upper()
    if u.endswith(".T"):
        return u
    return f"{u}.T" if u.isdigit() and 4 <= len(u) <= 5 else u


# ----------------
# JPXマップ参照
# ----------------
def _tse_lookup(ticker: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    return: (jp_name, sector, market) いずれも無ければ None
    """
    code = _norm_key(ticker)
    v = _TSE_MAP.get(code)
    if v is None:
        return None, None, None
    if isinstance(v, str):
        name = v.strip() or None
        return name, None, None
    # dict 期待
    name = (v.get("name") or "").strip() or None
    sector = (v.get("sector") or "").strip() or None
    market = (v.get("market") or "").strip() or None
    return name, sector, market


# ----------------
# 共通ヘルパー群
# ----------------
def _now_jst() -> datetime:
    return dj_now().astimezone(JST)

def _price_from_cache_or(ticker: str, fallback: Optional[int]) -> int:
    """
    PriceCache があれば優先。キーは 'XXXX' と 'XXXX.T' の両方を探す。
    """
    if PriceCache is not None:
        keys = { (ticker or "").upper() }
        keys.add(_display_ticker(ticker))
        for k in keys:
            pc = PriceCache.objects.filter(ticker=k).only("last_price").first()
            if pc and pc.last_price is not None:
                try:
                    return int(pc.last_price)
                except Exception:
                    pass
    return int(fallback or 3000)

def _credit_balance(user) -> int:
    """
    信用余力の推定。MarginState が無ければ Ledger 合算で代替。
    """
    qs = MarginState.objects.filter(
        account__in=BrokerAccount.objects.filter(account_type="信用", currency="JPY")
    )
    if not qs.exists():
        total = 0
        for a in BrokerAccount.objects.filter(account_type="信用", currency="JPY"):
            try:
                total += int(CashLedger.objects.filter(account=a).aggregate(s=Sum("amount"))["s"] or 0)
            except Exception:
                pass
        return max(0, total)

    latest = qs.values("account_id").annotate(m=Max("as_of"))
    s = 0
    for row in latest:
        st = qs.filter(account_id=row["account_id"], as_of=row["m"]).only("available_funds").first()
        if st and st.available_funds is not None:
            s += int(st.available_funds)
    return max(0, s)

def _latest_trends(user) -> List[TrendResult]:
    """
    銘柄ごとに最新1件だけに圧縮（'4755' と '4755.T' を同一視）
    """
    rows = TrendResult.objects.filter(user=user).order_by("-asof", "-updated_at")
    seen, out = set(), []
    for r in rows:
        key = _norm_key(r.ticker)
        if key in seen:
            continue
        seen.add(key)
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
        ticker=_display_ticker(tr.ticker),
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
    優先順位：1) JPXマップ → 2) TrendResult.name → 3) Holding/Watch → 4) ティッカー
    さらに：英名や未設定なら JPXの和名で静かに補完・保存
    """
    jp_name, sector, market = _tse_lookup(tr.ticker)
    if not jp_name:
        # フォールバック: TrendResult / Holding / Watch
        if (tr.name or "").strip():
            name = tr.name.strip()
        else:
            t_norm = _display_ticker(tr.ticker)
            h = Holding.objects.filter(user=user, ticker=t_norm).only("name").first()
            w = None if h and (h.name or "").strip() else WatchEntry.objects.filter(user=user, ticker=t_norm).only("name").first()
            name = (h.name if h and (h.name or "").strip() else (w.name if w and (w.name or "").strip() else t_norm))
        return str(name), sector, market

    # --- 攻めの改善：DBへ和名を自動補完 ---
    try:
        if (not tr.name) or any(ch.isascii() for ch in str(tr.name)):
            if tr.name != jp_name:
                tr.name = jp_name
                tr.save(update_fields=["name"])
    except Exception:
        pass

    return jp_name, sector, market


# ================
# カード生成
# ================
def _card_from(tr: TrendResult, pol: Dict[str, Any], credit: int) -> Dict[str, Any]:
    # 参考価格
    entry = _price_from_cache_or(tr.ticker, tr.entry_price_hint or tr.close_price)

    # 退出ターゲット（ATR/R/時間）をポリシーから算定
    xt = _exit_targets_from_policy(pol, tr, entry)
    tp_pct = float(pol["targets"]["tp_pct"])
    sl_pct = float(pol["targets"]["sl_pct"])

    # 価格が無ければ pct から計算、あれば優先
    tp_price = int(round(entry * (1 + tp_pct))) if xt["tp_price"] is None else int(xt["tp_price"])
    sl_price = int(round(entry * (1 - sl_pct))) if xt["sl_price"] is None else int(xt["sl_price"])

    # サイズ計算（リスク一定）
    risk_pct = float(pol["size"]["risk_pct"])
    stop_value = max(1, entry - sl_price)
    risk_budget = max(1, int(round(credit * risk_pct)))
    shares = risk_budget // stop_value if stop_value > 0 else 0
    need_cash = shares * entry if shares > 0 else None

    # 表示名の解決（JPXマップ→TrendResult/Holding/Watch→ティッカー）
    name, sector, market = _resolve_display(tr.user, tr)
    win_prob = float(tr.overall_score or 60) / 100.0  # overall→ざっくり勝率換算

    card: Dict[str, Any] = {
        "policy_id": pol["id"],
        "ticker": _display_ticker(tr.ticker),
        "name": str(name),
        "segment": pol["labels"]["segment"],
        "action":  pol["labels"]["action"],
        "reasons": [
            "Policy検知ベース",
            f"信頼度{int(round(float(tr.confidence or 0.5)*100))}%",
            f"slope≈{round(float(tr.slope_annual or 0.0)*100,1)}%/yr",
        ],
        "ai": {
            "win_prob": win_prob,
            "size_mult": float(tr.size_mult or 1.0),
        },
        "theme": {
            "id": "trend",
            "label": (tr.theme_label or "trend"),
            "score": float(tr.theme_score or 0.55),
        },
        "weekly_trend": (tr.weekly_trend or "flat"),
        "overall_score": int(tr.overall_score or 60),
        "entry_price_hint": entry,
        "targets": {
            "tp": f"目標 +{int(tp_pct*100)}%",
            "sl": f"損切り -{int(sl_pct*100)}%",
            "tp_pct": tp_pct, "sl_pct": sl_pct,
            "tp_price": tp_price, "sl_price": sl_price,
            "trail_atr_mult": xt.get("trail_atr_mult"),
            "time_exit_due": xt.get("time_exit_due", False),
        },
        "sizing": {
            "credit_balance": credit,
            "risk_per_trade": risk_pct,
            "position_size_hint": (shares if shares > 0 else None),
            "need_cash": need_cash,
        },
    }

    # あるならメタ情報を付与
    meta_extra: Dict[str, Any] = {}
    if sector: meta_extra["sector"] = sector
    if market: meta_extra["market"] = market
    if meta_extra:
        card["meta"] = meta_extra

    return card


def _apply_name_normalization(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    返却直前に、和名へ正規化し name を str に統一。
    ついでに sector / market を meta に格納（存在する場合）。
    """
    hs = payload.get("highlights") or []
    for h in hs:
        t = str(h.get("ticker") or "")
        jp_name, sector, market = _tse_lookup(t)
        fallback = h.get("name")
        if isinstance(fallback, dict):
            fallback = fallback.get("name") or ""
        name = jp_name or (fallback if fallback is not None else _display_ticker(t))
        h["name"] = str(name)

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
            "model_version": "v0.7-policy-exits+name+dedup",
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