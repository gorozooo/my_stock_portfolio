# advisor/views/api.py
import json
from datetime import datetime, timezone, timedelta
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now as dj_now
from advisor.models import ActionLog, Reminder, WatchEntry

# JST
JST = timezone(timedelta(hours=9))

def _log(*args):
    print("[advisor.api]", *args)

def _no_store(resp: JsonResponse) -> JsonResponse:
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp

# =============== ボード（モック） ===============
def board_api(request):
    jst_now = datetime.now(JST)
    data = {
        "meta": {
            "generated_at": jst_now.replace(hour=7, minute=25, second=0, microsecond=0).isoformat(),
            "model_version": "v0.1-mock",
            "adherence_week": 0.84,
            "regime": {"trend_prob": 0.63, "range_prob": 0.37, "nikkei": "↑", "topix": "→"},
        },
        "theme": {
            "week": "2025-W43",
            "top3": [
                {"id": "semiconductor", "label": "半導体", "score": 0.78},
                {"id": "travel", "label": "旅行", "score": 0.62},
                {"id": "banks", "label": "銀行", "score": 0.41},
            ],
        },
        "highlights": [
            {
                "ticker": "8035.T",
                "name": "東京エレクトロン",
                "segment": "中期（20〜45日）",
                "action": "買い候補（勢い強）",
                "reasons": ["半導体テーマが強い（78点）", "出来高が増えている（+35%）", "あなたの得意型（AI勝率82%）"],
                "targets": {"tp": "目標 +8〜12%", "sl": "損切り -3%"},
                "ai": {"win_prob": 0.82, "size_mult": 1.08},
                "theme": {"id": "semiconductor", "label": "半導体", "score": 0.78},
            },
            {
                "ticker": "7203.T",
                "name": "トヨタ",
                "segment": "中期（20〜45日）",
                "action": "30日目 → 一部売り",
                "reasons": ["保有日数の区切り", "自動車テーマ 65点", "最近は横ばい"],
                "targets": {"tp": "前回高値付近で1/3売り", "sl": "週の目安を割ったら縮小"},
                "ai": {"win_prob": 0.64, "size_mult": 0.96},
                "theme": {"id": "auto", "label": "自動車", "score": 0.65},
            },
            {
                "ticker": "6758.T",
                "name": "ソニーG",
                "segment": "短期（5〜10日）",
                "action": "買い候補（短期の勢い）",
                "reasons": ["出来高が増えている", "戻りが強い", "AI勝率74%"],
                "targets": {"tp": "+4〜6%で半分利確", "sl": "-2%で撤退"},
                "ai": {"win_prob": 0.74, "size_mult": 1.05},
                "theme": {"id": "electronics", "label": "電機", "score": 0.58},
            },
            {
                "ticker": "8267.T",
                "name": "イオン",
                "segment": "NISA（長期）",
                "action": "配当・優待目的で継続",
                "reasons": ["決算前の確認", "生活必需で安定", "分散の役割"],
                "targets": {"tp": "長期保有が基本", "sl": "想定範囲外なら縮小"},
                "ai": {"win_prob": 0.60, "size_mult": 1.00},
                "theme": {"id": "retail", "label": "小売", "score": 0.55},
            },
            {
                "ticker": "8306.T",
                "name": "三菱UFJ",
                "segment": "中期（20〜45日）",
                "action": "買い候補（銀行）",
                "reasons": ["銀行テーマ 41点（様子見寄り）", "値動きは安定", "分散の候補"],
                "targets": {"tp": "+5〜8%で段階利確", "sl": "-3%で撤退"},
                "ai": {"win_prob": 0.61, "size_mult": 0.92},
                "theme": {"id": "banks", "label": "銀行", "score": 0.41},
            },
        ],
    }
    return _no_store(JsonResponse(data, json_dumps_params={"ensure_ascii": False}))

# =============== ActionLog ===============
@csrf_exempt
def record_action(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    try:
        raw = request.body.decode("utf-8") if request.body else "{}"
        payload = json.loads(raw or "{}")

        # ★ 未ログインなら 401 を返す（ここで失敗理由を明示）
        if not (hasattr(request, "user") and request.user and request.user.is_authenticated):
            return _no_store(JsonResponse(
                {"ok": False, "error": "auth_required"},
                status=401
            ))

        user = request.user
        _log("record_action payload=", payload, "user=", getattr(user, "username", None))

        # ActionLog（user 必須モデルでも安全）
        log = ActionLog.objects.create(
            user=user,
            ticker=(payload.get("ticker") or "").strip(),
            policy_id=payload.get("policy_id", "") or "",
            action=payload.get("action", "") or "",
            note=payload.get("note", "") or "",
        )
        _log("record_action saved id=", log.id)

        # ウォッチリスト upsert（save_order のとき）
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
                    "theme_score": payload.get("theme_score"),
                    "ai_win_prob": payload.get("ai_win_prob"),
                    "target_tp": payload.get("target_tp", "") or "",
                    "target_sl": payload.get("target_sl", "") or "",
                    "in_position": False,
                },
            )
            _log("record_action → WatchEntry upsert (board reasons copied)")

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
            return _no_store(JsonResponse(
                {"ok": False, "error": "auth_required"},
                status=401
            ))

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