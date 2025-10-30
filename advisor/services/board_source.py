# advisor/services/board_source.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import os
import json

from django.db.models import Max
from django.utils.timezone import now as dj_now
from django.conf import settings
from django.contrib.auth import get_user_model

from advisor.models_trend import TrendResult
from portfolio.models_cash import MarginState, BrokerAccount, CashLedger
from portfolio.models import Holding
from advisor.models import WatchEntry

# ある場合はキャッシュモデル
try:
    from advisor.models_cache import PriceCache, BoardCache
except Exception:
    PriceCache = None  # type: ignore
    BoardCache = None  # type: ignore

# ポリシー
from .policy_loader import load_active_policies

User = get_user_model()
JST = timezone(timedelta(hours=9))

# ==============================
# JPX銘柄名マップ（tse_list.json）
# ==============================
# data/tse_list.json を一度だけ読み込み（存在しなくてもOK）
def _load_tse_map() -> Dict[str, str]:
    # settings.BASE_DIR 直下の data/tse_list.json を想定
    base_dir = getattr(settings, "BASE_DIR", os.getcwd())
    path = os.path.join(base_dir, "data", "tse_list.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = json.load(f)
            # 例: {"8035": "東京エレクトロン"} のフラット辞書
            if isinstance(m, dict):
                # すべて文字列キーに統一
                return {str(k): str(v) for k, v in m.items() if v}
    except Exception:
        pass
    return {}

_TSE_MAP: Dict[str, str] = _load_tse_map()

def _jp_name_from_tse_map(ticker: str, fallback: Optional[str] = None) -> str:
    """
    TSEマップで日本語名を最優先採用。
    例: '8035.T' -> '8035' をキーに検索。無ければ fallback -> ticker。
    """
    t = str(ticker).upper().strip()
    if t.endswith(".T"):
        t = t[:-2]
    return _TSE_MAP.get(t) or (fallback or str(ticker).upper())

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
    # 信用口座の「最新 available_funds 合計」→無ければ台帳合計
    qs = MarginState.objects.filter(
        account__in=BrokerAccount.objects.filter(account_type="信用", currency="JPY")
    )
    if not qs.exists():
        total = 0
        for a in BrokerAccount.objects.filter(account_type="信用", currency="JPY"):
            # aggregateが環境差で違っても落ちないようガード
            try:
                from django.db.models import Sum
                led = CashLedger.objects.filter(account=a).aggregate(s=Sum("amount"))["s"] or 0
            except Exception:
                led = 0
            total += int(led or 0)
        return max(0, total)

    latest = qs.values("account_id").annotate(m=Max("as_of"))
    total_m = 0
    for row in latest:
        st = qs.filter(account_id=row["account_id"], as_of=row["m"]).first()
        if st and st.available_funds is not None:
            total_m += int(st.available_funds)
    return max(0, total_m)

def _latest_trends(user) -> List[TrendResult]:
    """
    銘柄ごとに直近 asof の1件を返す（DB機能差を吸収するフォールバック）。
    """
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
    # overall
    if tr.overall_score is not None and int(tr.overall_score) < int(r["min_overall"]):
        return False
    # theme
    if tr.theme_score is not None and float(tr.theme_score) < float(r["min_theme"]):
        return False
    # weekly trend
    if tr.weekly_trend and tr.weekly_trend not in r["allow_weekly"]:
        return False
    # slope
    if r.get("min_slope_yr") is not None:
        s = float(tr.slope_annual or 0.0)
        if s < float(r["min_slope_yr"]):
            return False
    return True

def _display_name(user, ticker: str, trend_name: Optional[str]) -> str:
    """
    表示名の優先順位（日本語最優先）：
      1) JPX辞書（tse_list.json）
      2) TrendResult.name
      3) Holding.name
      4) WatchEntry.name
      5) ティッカー
    """
    # 1) JPXマップを最優先（強制和名化）
    jp = _jp_name_from_tse_map(ticker, None)
    if jp:
        return jp

    # 以降は保険（JPXに無いもの用）
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

def _card_from(tr: TrendResult, pol: Dict[str, Any], credit: int) -> Dict[str, Any]:
    tp_pct = float(pol["targets"]["tp_pct"])
    sl_pct = float(pol["targets"]["sl_pct"])
    entry = _price_from_cache_or(tr.ticker, tr.entry_price_hint or tr.close_price)

    tp_price = int(round(entry * (1 + tp_pct)))
    sl_price = int(round(entry * (1 - sl_pct)))

    risk_pct = float(pol["size"]["risk_pct"])
    stop_value = max(1, entry - sl_price)
    risk_budget = max(1, int(round(credit * risk_pct)))
    shares = risk_budget // stop_value if stop_value > 0 else 0
    need_cash = shares * entry if shares > 0 else None

    name = _display_name(tr.user, tr.ticker, tr.name)
    win_prob = float(tr.overall_score or 60) / 100.0

    return {
        "policy_id": pol["id"],
        "ticker": tr.ticker.upper(),
        "name": name,  # ★ 初回から日本語で出す
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
            "position_size_hint": (shares if shares > 0 else None),
            "need_cash": need_cash,
        },
    }

def _apply_jp_name_to_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    既存キャッシュを読むときも、強制的に日本語名へ置換。
    """
    hs = payload.get("highlights") or []
    for h in hs:
        t = h.get("ticker") or ""
        cur = h.get("name") or ""
        h["name"] = _jp_name_from_tse_map(t, cur)
    return payload

# =====================
# 公開エントリポイント
# =====================
def build_board(user, *, use_cache: bool = True) -> Dict[str, Any]:
    """
    ポリシー駆動：
      1) キャッシュ（あればJP名に補正）
      2) ポリシー読込
      3) 最新TrendResultを全銘柄から取得→各ポリシーでフィルタ→並べ替え→上位5件
      4) 生成したpayloadをキャッシュ保存（JP名で固定化）
    """
    # 1) キャッシュ
    if use_cache and BoardCache is not None:
        bc = BoardCache.objects.filter(user=user).first() or BoardCache.objects.filter(user__isnull=True).first()
        if bc and bc.is_fresh:
            payload = dict(bc.payload)
            meta = payload.setdefault("meta", {})
            meta["live"] = True
            # ★ キャッシュ経由でも必ず日本語化
            return _apply_jp_name_to_payload(payload)

    # 2) ポリシー
    policies = load_active_policies()
    credit = _credit_balance(user)
    now = _now_jst()

    # 3) Trend → policy 適合 → スコア付け
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
            "source_breakdown": {
                "policies": {p["id"]: sum(1 for h in highlights if h.get("policy_id") == p["id"]) for p in policies}
            },
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

    # 念のためここでも日本語名で正規化（将来の変更漏れ対策）
    data = _apply_jp_name_to_payload(data)

    # 4) キャッシュ保存
    if use_cache and BoardCache is not None:
        try:
            BoardCache.objects.create(user=user, payload=data, generated_at=now, ttl_minutes=180, note="policy")
        except Exception:
            pass

    return data