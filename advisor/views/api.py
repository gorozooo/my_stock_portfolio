# advisor/views/api.py
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta, date
from typing import Dict, Any, List, Tuple, Optional

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now as dj_now
from django.db.models import Max

from advisor.models import ActionLog, Reminder, WatchEntry
# ★追加：実データを使うためのモデル
from advisor.models import TrendResult  # 存在しない場合は後でサービスorデモにフォールバック
from portfolio.models import UserSetting  # 口座残高・リスク％（無ければ既定値）

# ===== JST =====
JST = timezone(timedelta(hours=9))


def _log(*args):
    print("[advisor.api]", *args)


def _no_store(resp: JsonResponse) -> JsonResponse:
    """スマホブラウザのキャッシュを抑止"""
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp

# 追加
def _normalize_ticker(t: str) -> str:
    t = (t or "").strip().upper()
    if not t:
        return t
    if t.isdigit() and 4 <= len(t) <= 5:
        return f"{t}.T"
    return t

# ====== （将来の実データ）services.board_source があればそちらを使う ======
# ない場合は、下のローカルロジック _build_board_local() を使う
try:
    from advisor.services.board_source import build_board as _build_board_service  # type: ignore
    _HAS_SERVICE = True
except Exception:
    _build_board_service = None
    _HAS_SERVICE = False


# ====== ローカル（デモ用フォールバック） ======
_FALLBACK_PRICE = {
    "8035.T": 12450,
    "7203.T": 3150,
    "6758.T": 14680,
    "8267.T": 3180,
    "8306.T": 1470,
}


def _last_price(ticker: str) -> int:
    return int(_FALLBACK_PRICE.get(ticker.upper(), 3000))


def _tp_sl_pct(segment: str) -> Tuple[float, float]:
    s = segment or ""
    if "短期" in s:
        return 0.06, 0.02
    if "中期" in s:
        return 0.10, 0.03
    return 0.12, 0.05


def _weekly_trend(theme_score: float, win_prob: float) -> str:
    score = 0.7 * win_prob + 0.3 * theme_score
    if score >= 0.62:
        return "up"
    if score >= 0.48:
        return "flat"
    return "down"


def _overall(theme_score: float, win_prob: float) -> int:
    return int(round((0.7 * win_prob + 0.3 * theme_score) * 100))


def _tp_sl_prob(win_prob: float) -> Tuple[float, float]:
    # デモ用の暫定配分
    tp = max(0.0, min(1.0, win_prob * 0.46))
    sl = max(0.0, min(1.0, (1.0 - win_prob) * 0.30))
    return tp, sl


def _position_size(
    entry: int, sl_price: int, credit_balance: Optional[int], risk_per_trade: float
) -> Tuple[Optional[int], Optional[int]]:
    if not credit_balance or entry <= 0:
        return None, None
    stop_value = max(1, entry - sl_price)  # 円
    risk_budget = max(1, int(round(credit_balance * risk_per_trade)))
    shares = risk_budget // stop_value
    if shares <= 0:
        return None, None
    need_cash = shares * entry
    return shares, need_cash


# ===== 追加：UserSetting（口座残高・リスク％）の取得 =====
def _get_user_risk_params(user) -> Tuple[int, float]:
    """
    UserSetting があればそれを使用。無ければデモ既定値。
    - 口座残高: account_equity（円）
    - 1トレードのリスク％: risk_pct（% → 0-1に変換）
    （安全ガードとして 0.1%〜5% に丸め）
    """
    try:
        if user and user.is_authenticated:
            us = UserSetting.objects.get(user=user)
            equity = int(us.account_equity or 1_000_000)
            risk01 = float(us.risk_pct or 1.0) / 100.0
            risk01 = max(0.001, min(0.05, risk01))
            return equity, risk01
    except Exception:
        pass
    return 1_000_000, 0.01  # 既定（従来デモと同等）


# ===== 追加：TrendResult → ハイライト1件へ整形 =====
def _trend_to_highlight(
    tr, credit_balance: int, risk_per_trade: float
) -> Dict[str, Any]:
    last = int(tr.entry_price_hint or tr.close_price or 3000)
    win_prob = float(tr.win_prob or 0.6)
    theme_score = float(tr.theme_score or 0.5)
    overall = int(tr.overall_score) if tr.overall_score is not None else _overall(theme_score, win_prob)

    tp_pct, sl_pct = _tp_sl_pct("中期（20〜45日）")
    tp_price = int(round(last * (1 + tp_pct)))
    sl_price = int(round(last * (1 - sl_pct)))
    tp_prob, sl_prob = _tp_sl_prob(win_prob)

    size, need_cash = _position_size(last, sl_price, credit_balance, risk_per_trade)

    name = (tr.name or tr.ticker or "").strip() or tr.ticker  # 最終フォールバック

    return {
        "ticker": tr.ticker,
        "name": name,
        "segment": "中期（20〜45日）",
        "action": "ウォッチ候補",
        "reasons": [
            f"AI勝率 {int(round(win_prob * 100))}%",
            f"{(tr.theme_label or 'テーマ')} {int(round(theme_score * 100))}点",
            {"up": "上向き", "flat": "横ばい", "down": "下向き"}.get((tr.weekly_trend or "flat").lower(), "横ばい"),
        ],
        "ai": {
            "win_prob": win_prob,
            "size_mult": float(tr.size_mult or 1.0),
            "tp_prob": tp_prob,
            "sl_prob": sl_prob,
        },
        "theme": {
            "id": (tr.theme_label or "theme").lower(),
            "label": tr.theme_label or "テーマ",
            "score": theme_score,
        },
        "weekly_trend": (tr.weekly_trend or "flat").lower(),
        "overall_score": overall,
        "entry_price_hint": last,
        "targets": {
            "tp": f"目標 +{int(tp_pct * 100)}%",
            "sl": f"損切り -{int(sl_pct * 100)}%",
            "tp_pct": tp_pct, "sl_pct": sl_pct,
            "tp_price": tp_price, "sl_price": sl_price,
        },
        "sizing": {
            "credit_balance": credit_balance,
            "risk_per_trade": risk_per_trade,
            "position_size_hint": size,
            "need_cash": need_cash,
        },
    }


def _theme_top3_from_trends(qs: List[TrendResult]) -> List[Dict[str, Any]]:
    """TrendResult 集合からテーマTop3（平均スコア高い順）をざっくり算出"""
    buckets: Dict[str, List[float]] = {}
    for tr in qs:
        if tr.theme_label and tr.theme_score is not None:
            buckets.setdefault(tr.theme_label, []).append(float(tr.theme_score))
    items = [
        {"id": k.lower(), "label": k, "score": (sum(v) / len(v) if v else 0.0)}
        for k, v in buckets.items()
    ]
    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:3]


def _build_board_from_trends(user) -> Optional[Dict[str, Any]]:
    """
    TrendResult があれば実データでボードを構成。
    - user があれば user 限定、無ければ全体から直近 asof を採用
    - 常に 5 件まで返す（不足時はその分だけ）
    """
    from django.apps import apps  # ★ ここでローカルimport（以前は未importで落ち得る）
    TrendResult = apps.get_model("advisor", "TrendResult")

    qs_base = TrendResult.objects.all()
    if user and getattr(user, "is_authenticated", False):
        qs_base = qs_base.filter(user=user)

    latest = qs_base.aggregate(m=Max("asof"))["m"]
    if not latest:
        return None

    qs = list(
        qs_base.filter(asof=latest)
        .order_by("-overall_score", "-confidence")[:5]
    )
    if not qs:
        return None

    credit_balance, risk_per_trade = _get_user_risk_params(user)
    highlights = [_trend_to_highlight(tr, credit_balance, risk_per_trade) for tr in qs]
    theme_top3 = _theme_top3_from_trends(qs)

    jst_now = datetime.now(JST)
    data: Dict[str, Any] = {
        "meta": {
            "generated_at": jst_now.replace(second=0, microsecond=0).isoformat(),
            "model_version": "v0.4-trend-first+cached",
            "adherence_week": 0.84,
            "regime": {"trend_prob": 0.60, "range_prob": 0.40, "nikkei": "↑", "topix": "→"},
            "scenario": "TrendResult最優先で今日の候補を生成（実データ）",
            "pairing": {"id": 2, "label": "順張り・短中期"},
            "self_mirror": {"recent_drift": "—"},
            "credit_balance": credit_balance,
            "live": True,
        },
        "theme": {
            "week": jst_now.strftime("%Y-W%U"),
            "top3": theme_top3,
        },
        "highlights": highlights,
    }
    return data


def _build_board_local(user) -> Dict[str, Any]:
    """いままでのローカル（デモ）生成ロジックをそのまま温存"""
    jst_now = datetime.now(JST)

    # デモ用の信用余力（本番はユーザーの実データに差し替え）
    credit_balance = 1_000_000
    risk_per_trade = 0.01  # 1%

    base_items: List[Dict[str, Any]] = [
        {
            "ticker": "8035.T",
            "name": "東京エレクトロン",
            "segment": "中期（20〜45日）",
            "action": "買い候補（勢い強）",
            "reasons": ["半導体テーマが強い（78点）", "出来高が増えている（+35%）", "あなたの得意型（AI勝率82%）"],
            "ai": {"win_prob": 0.82, "size_mult": 1.08},
            "theme": {"id": "semiconductor", "label": "半導体", "score": 0.78},
        },
        {
            "ticker": "7203.T",
            "name": "トヨタ",
            "segment": "中期（20〜45日）",
            "action": "30日目 → 一部売り",
            "reasons": ["保有日数の区切り", "自動車テーマ 65点", "最近は横ばい"],
            "ai": {"win_prob": 0.64, "size_mult": 0.96},
            "theme": {"id": "auto", "label": "自動車", "score": 0.65},
        },
        {
            "ticker": "6758.T",
            "name": "ソニーG",
            "segment": "短期（5〜10日）",
            "action": "買い候補（短期の勢い）",
            "reasons": ["出来高が増えている", "戻りが強い", "AI勝率74%"],
            "ai": {"win_prob": 0.74, "size_mult": 1.05},
            "theme": {"id": "electronics", "label": "電機", "score": 0.58},
        },
        {
            "ticker": "8267.T",
            "name": "イオン",
            "segment": "NISA（長期）",
            "action": "配当・優待目的で継続",
            "reasons": ["決算前の確認", "生活必需で安定", "分散の役割"],
            "ai": {"win_prob": 0.60, "size_mult": 1.00},
            "theme": {"id": "retail", "label": "小売", "score": 0.55},
        },
        {
            "ticker": "8306.T",
            "name": "三菱UFJ",
            "segment": "中期（20〜45日）",
            "action": "買い候補（銀行）",
            "reasons": ["銀行テーマ 41点（様子見寄り）", "値動きは安定", "分散の候補"],
            "ai": {"win_prob": 0.61, "size_mult": 0.92},
            "theme": {"id": "banks", "label": "銀行", "score": 0.41},
        },
    ]

    highlights: List[Dict[str, Any]] = []
    for it in base_items:
        last = _last_price(it["ticker"])
        tp_pct, sl_pct = _tp_sl_pct(it["segment"])
        tp_price = int(round(last * (1 + tp_pct)))
        sl_price = int(round(last * (1 - sl_pct)))

        win_prob = float(it["ai"]["win_prob"])
        theme_score = float(it["theme"]["score"])
        weekly = _weekly_trend(theme_score, win_prob)
        overall = _overall(theme_score, win_prob)
        tp_prob, sl_prob = _tp_sl_prob(win_prob)
        size, need_cash = _position_size(last, sl_price, credit_balance, risk_per_trade)

        ext = {
            **it,
            "weekly_trend": weekly,
            "overall_score": overall,
            "entry_price_hint": last,
            "targets": {
                "tp": it.get("targets", {}).get("tp", f"目標 +{int(tp_pct*100)}%"),
                "sl": it.get("targets", {}).get("sl", f"損切り -{int(sl_pct*100)}%"),
                "tp_pct": tp_pct,
                "sl_pct": sl_pct,
                "tp_price": tp_price,
                "sl_price": sl_price,
            },
            "sizing": {
                "credit_balance": credit_balance,
                "risk_per_trade": risk_per_trade,
                "position_size_hint": size,
                "need_cash": need_cash,
            },
            "ai": {
                **it["ai"],
                "tp_prob": tp_prob,
                "sl_prob": sl_prob,
            },
        }
        highlights.append(ext)

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": jst_now.replace(hour=7, minute=25, second=0, microsecond=0).isoformat(),
            "model_version": "v0.2-demo-policy-lite",
            "adherence_week": 0.84,
            "regime": {"trend_prob": 0.63, "range_prob": 0.37, "nikkei": "↑", "topix": "→"},
            "scenario": "半導体に資金回帰。短期は押し目継続、週足↑",
            "pairing": {"id": 2, "label": "順張り・短中期"},
            "self_mirror": {"recent_drift": "損切り未実施 3/4件"},
            "credit_balance": credit_balance,
            "live": False,  # ローカルは常に False
        },
        "theme": {
            "week": "2025-W43",
            "top3": [
                {"id": "semiconductor", "label": "半導体", "score": 0.78},
                {"id": "travel", "label": "旅行", "score": 0.62},
                {"id": "banks", "label": "銀行", "score": 0.41},
            ],
        },
        "highlights": highlights,
    }
    return data


# =============== /advisor/api/board/ ===============
def board_api(request):
    """
    優先順位:
      1) services.board_source.build_board(user)   （force=1 で use_cache=False を試行）
      2) TrendResult（直近 asof）
      3) デモ固定
    いずれかで必ず返す。キャッシュは no-store。
    """
    user = getattr(request, "user", None)
    force = (request.GET.get("force") == "1")

    # 1) サービス実装があれば最優先（失敗してもフォールバック）
    if _HAS_SERVICE and callable(_build_board_service):
        try:
            if "use_cache" in getattr(_build_board_service, "__code__", type("", (), {"co_varnames": ()})).co_varnames:
                data = _build_board_service(user, use_cache=not force)  # type: ignore
            else:
                data = _build_board_service(user)  # type: ignore
            if isinstance(data, dict):
                data.setdefault("meta", {}).setdefault("live", True)
                data = _normalize_payload_names(data)  # ★ 和名へ最終正規化
                return _no_store(JsonResponse(data, json_dumps_params={"ensure_ascii": False}))
        except Exception as e:
            _log("board_api: service failed → fallback. err=", repr(e))

    # 2) TrendResult から構成
    try:
        data = _build_board_from_trends(user)
        if data:
            data = _normalize_payload_names(data)  # ★ 和名へ最終正規化
            return _no_store(JsonResponse(data, json_dumps_params={"ensure_ascii": False}))
    except Exception as e:
        _log("board_api: trend fallback failed → demo. err=", repr(e))

    # 3) デモ固定
    data = _build_board_local(user)
    data = _normalize_payload_names(data)  # ★ 和名へ最終正規化
    return _no_store(JsonResponse(data, json_dumps_params={"ensure_ascii": False}))


# =============== ActionLog（＋save時にWatchEntryへコピー） ===============
@csrf_exempt
def record_action(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    try:
        raw = request.body.decode("utf-8") if request.body else "{}"
        payload = json.loads(raw or "{}")

        # 未ログインは 401
        if not (hasattr(request, "user") and request.user and request.user.is_authenticated):
            return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))
        user = request.user

        _log("record_action payload=", payload, "user=", getattr(user, "username", None))

        # まずアクションログは素直に保存
        log = ActionLog.objects.create(
            user=user,
            ticker=(payload.get("ticker") or "").strip().upper(),
            policy_id=payload.get("policy_id", "") or "",
            action=payload.get("action", "") or "",
            note=payload.get("note", "") or "",
        )
        _log("record_action saved id=", log.id)

        # ---- WatchEntry upsert（ホワイトリストで安全に）----
        # 置き換え（該当ブロックのみ）
        if payload.get("action") == "save_order":
            tkr = _normalize_ticker(payload.get("ticker") or "")
        
            allowed: Dict[str, Any] = {
                "name": payload.get("name", "") or "",
                "note": payload.get("note", "") or "",
                "reason_summary": payload.get("reason_summary", "") or "",
                "reason_details": payload.get("reason_details", []) or [],
                "theme_label": payload.get("theme_label", "") or "",
                "theme_score": payload.get("theme_score", None),
                "ai_win_prob": payload.get("ai_win_prob", None),
                "target_tp": payload.get("target_tp", "") or "",
                "target_sl": payload.get("target_sl", "") or "",
                "overall_score": payload.get("overall_score", None),
                "weekly_trend": payload.get("weekly_trend", "") or "",
                "entry_price_hint": payload.get("entry_price_hint", None),
                "tp_price": payload.get("tp_price", None),
                "sl_price": payload.get("sl_price", None),
                "tp_pct": payload.get("tp_pct", None),
                "sl_pct": payload.get("sl_pct", None),
                "position_size_hint": payload.get("position_size_hint", None),
                "in_position": False,
            }
        
            WatchEntry.objects.update_or_create(
                user=user,
                ticker=tkr,  # ★ 正規化後を保存
                status=WatchEntry.STATUS_ACTIVE,
                defaults=allowed,
            )
            _log("record_action → WatchEntry upsert OK")

        return _no_store(JsonResponse({"ok": True, "id": log.id}))

    except Exception as e:
        _log("record_action ERROR:", repr(e))
        return _no_store(JsonResponse({"ok": False, "error": str(e)}, status=400))


# =============== Reminder ===============
@csrf_exempt
def create_reminder(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    try:
        raw = request.body.decode("utf-8") if request.body else "{}"
        payload = json.loads(raw or "{}")

        if not (hasattr(request, "user") and request.user and request.user.is_authenticated):
            return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))

        user = request.user
        minutes = int(payload.get("after_minutes", 120))
        fire_at = datetime.now(JST) + timedelta(minutes=minutes)

        _log("create_reminder payload=", payload, "user=", getattr(user, "username", None), "fire_at=", fire_at)

        r = Reminder.objects.create(
            user=user,
            ticker=(payload.get("ticker") or "").strip().upper(),
            message=f"{payload.get('ticker','')} をもう一度チェック",
            fire_at=fire_at,
        )
        _log("create_reminder saved id=", r.id)
        return _no_store(JsonResponse({"ok": True, "id": r.id, "fire_at": fire_at.isoformat()}))

    except Exception as e:
        _log("create_reminder ERROR:", repr(e))
        return _no_store(JsonResponse({"ok": False, "error": str(e)}, status=400))


# =============== デバッグ用 ===============
def ping(request):
    return _no_store(JsonResponse({"ok": True, "now": dj_now().astimezone(JST).isoformat()}))


@csrf_exempt
def debug_add(request):
    log = ActionLog.objects.create(ticker="DEBUG.T", action="save_order", note="debug via GET")
    _log("debug_add saved id=", log.id)
    return _no_store(JsonResponse({"ok": True, "id": log.id}))


@csrf_exempt
def debug_add_reminder(request):
    r = Reminder.objects.create(
        ticker="DEBUG.T",
        message="debug",
        fire_at=dj_now().astimezone(JST) + timedelta(minutes=1),
    )
    _log("debug_add_reminder saved id=", r.id)
    return _no_store(JsonResponse({"ok": True, "id": r.id}))
    

def _jpx_lookup(ticker: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    data/tse_list.json を用いて JPX和名/sector/market を返す。
    値は "名前" でも {"name":..., "sector":..., "market":...} でもOK。
    """
    try:
        from django.conf import settings
        import os, json
        base = getattr(settings, "BASE_DIR", os.getcwd())
        path = os.path.join(base, "data", "tse_list.json")
        cache = getattr(_jpx_lookup, "_cache", None)
        if cache is None:
            with open(path, "r", encoding="utf-8") as f:
                cache = json.load(f) if f else {}
            _jpx_lookup._cache = cache  # type: ignore[attr-defined]
        t = str(ticker).upper().strip()
        if t.endswith(".T"): t = t[:-2]
        v = cache.get(t)
        if v is None: return None, None, None
        if isinstance(v, str): return (v or None), None, None
        return (str(v.get("name") or "") or None,
                (str(v.get("sector") or "") or None) if "sector" in v else None,
                (str(v.get("market") or "") or None) if "market" in v else None)
    except Exception:
        return None, None, None


def _normalize_payload_names(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    highlights[].name を JPX和名で強制、nameはstr、sector/marketをmetaへ。
    """
    hs = payload.get("highlights") or []
    for h in hs:
        t = str(h.get("ticker") or "").upper()
        jp, sector, market = _jpx_lookup(t)
        base = h.get("name")
        if isinstance(base, dict):
            base = base.get("name") or ""
        name = jp or (base if base is not None else t)
        h["name"] = str(name)

        meta = dict(h.get("meta") or {})
        if jp:     meta.setdefault("jpx_name", jp)
        if sector: meta["sector"] = sector
        if market: meta["market"] = market
        if meta:   h["meta"] = meta
    return payload
