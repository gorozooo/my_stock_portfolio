# advisor/views/api.py
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple, Optional

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now as dj_now

from advisor.models import ActionLog, Reminder, WatchEntry

# JST
JST = timezone(timedelta(hours=9))


def _log(*args):
    print("[advisor.api]", *args)


def _no_store(resp: JsonResponse) -> JsonResponse:
    """スマホブラウザのキャッシュを抑止"""
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp


# ====== 内部ヘルパ（デモ用の簡易ロジック：本番は価格取得/モデル出力に差し替え） ======
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
        return 0.06, 0.02   # +6% / -2%
    if "中期" in s:
        return 0.10, 0.03   # +10% / -3%
    # 長期/NISAなど
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


def _position_size(entry: int, sl_price: int, credit_balance: Optional[int], risk_per_trade: float) -> Tuple[Optional[int], Optional[int]]:
    if not credit_balance or entry <= 0:
        return None, None
    stop_value = max(1, entry - sl_price)  # 円
    risk_budget = max(1, int(round(credit_balance * risk_per_trade)))
    shares = risk_budget // stop_value
    if shares <= 0:
        return None, None
    need_cash = shares * entry
    return shares, need_cash


# =============== ボード（モック＋簡易計算で拡張フィールド付与） ===============
def board_api(request):
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
                # 既存のテキスト（互換）
                "tp": it["targets"]["tp"] if "targets" in it and "tp" in it["targets"] else f"目標 +{int(tp_pct*100)}%",
                "sl": it["targets"]["sl"] if "targets" in it and "sl" in it["targets"] else f"損切り -{int(sl_pct*100)}%",
                # 追加の数値情報
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
            # 拡張アイデアのヘッダ表示（デモ文）
            "scenario": "半導体に資金回帰。短期は押し目継続、週足↑",
            "pairing": {"id": 2, "label": "順張り・短中期"},
            "self_mirror": {"recent_drift": "損切り未実施 3/4件"},
            "credit_balance": credit_balance,
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
    return _no_store(JsonResponse(data, json_dumps_params={"ensure_ascii": False}))


# =============== ActionLog（＋save時にWatchEntryへコピー） ===============
@csrf_exempt
def record_action(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    try:
        raw = request.body.decode("utf-8") if request.body else "{}"
        payload = json.loads(raw or "{}")

        # ★ 未ログインは明示 401 を返す
        if not (hasattr(request, "user") and request.user and request.user.is_authenticated):
            return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))

        user = request.user
        _log("record_action payload=", payload, "user=", getattr(user, "username", None))

        log = ActionLog.objects.create(
            user=user,
            ticker=(payload.get("ticker") or "").strip().upper(),
            policy_id=payload.get("policy_id", "") or "",
            action=payload.get("action", "") or "",
            note=payload.get("note", "") or "",
        )
        _log("record_action saved id=", log.id)

        # 📝 「メモする」→ WatchEntry を最新値でupsert（理由や数値を保存）
        if payload.get("action") == "save_order":
            tkr = (payload.get("ticker") or "").strip().upper()
            WatchEntry.objects.update_or_create(
                user=user,
                ticker=tkr,
                status=WatchEntry.STATUS_ACTIVE,
                defaults={
                    "name": payload.get("name", "") or "",
                    "note": payload.get("note", "") or "",
                    "reason_summary": payload.get("reason_summary", "") or "",
                    "reason_details": payload.get("reason_details", []) or [],
                    "theme_label": payload.get("theme_label", "") or "",
                    "theme_score": payload.get("theme_score", None),
                    "ai_win_prob": payload.get("ai_win_prob", None),
                    "target_tp": payload.get("target_tp", "") or "",
                    "target_sl": payload.get("target_sl", "") or "",
                    # 追加保管（将来の再計算/表示同期用）
                    "overall_score": payload.get("overall_score", None),
                    "weekly_trend": payload.get("weekly_trend", "") or "",
                    "entry_price_hint": payload.get("entry_price_hint", None),
                    "tp_price": payload.get("tp_price", None),
                    "sl_price": payload.get("sl_price", None),
                    "tp_pct": payload.get("tp_pct", None),
                    "sl_pct": payload.get("sl_pct", None),
                    "position_size_hint": payload.get("position_size_hint", None),
                    "in_position": False,
                },
            )
            _log("record_action → WatchEntry upsert (with reasons & numeric fields)")

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

        # ★ 未ログインは明示 401
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