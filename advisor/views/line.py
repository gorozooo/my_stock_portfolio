from __future__ import annotations
import json, os, hmac, hashlib, base64
from datetime import datetime, timedelta, timezone

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now as dj_now
from django.contrib.auth import get_user_model

from advisor.models import ActionLog

JST = timezone(timedelta(hours=9))

def _verify_signature(request: HttpRequest) -> bool:
    secret = os.getenv("LINE_CHANNEL_SECRET")
    if not secret:
        return True  # 環境に無ければ検証スキップ（開発用）
    sig = request.headers.get("X-Line-Signature", "")
    raw = request.body
    mac = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    calc = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(calc, sig)

def _actor():
    U = get_user_model()
    return U.objects.first()

def _ok():
    return JsonResponse({"ok": True})

def _save_action(user, ticker: str, action: str, note: str = ""):
    ActionLog.objects.create(user=user, ticker=ticker.upper(), action=action, note=note)

@csrf_exempt
def webhook(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("OK")
    if not _verify_signature(request):
        return HttpResponse(status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponse(status=400)

    user = _actor()
    if not user:
        return _ok()

    events = payload.get("events") or []
    for ev in events:
        et = ev.get("type")

        # ===== Postback（ボタン押下） =====
        if et == "postback":
            data = (ev.get("postback") or {}).get("data") or ""
            # 例: save:7203.T / reject:6758.T / snooze:8035.T:120
            parts = data.split(":")
            kind = parts[0] if parts else ""
            ticker = (parts[1] if len(parts) > 1 else "").upper()
            if not ticker:
                continue

            if kind == "save":
                _save_action(user, ticker, "save_order", "from_line_button")
            elif kind == "reject":
                _save_action(user, ticker, "reject", "from_line_button")
            elif kind == "snooze":
                mins = 120
                try:
                    if len(parts) > 2:
                        mins = int(parts[2])
                except Exception:
                    pass
                until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                # クールダウン代わりに "notify" を直近扱いで記録
                _save_action(user, ticker, "notify", f"snooze_until={until.isoformat()}")
            else:
                _save_action(user, ticker, "unknown", data)
            continue

        # ===== 任意：テキストコマンド（/save など） =====
        if et == "message" and (ev.get("message") or {}).get("type") == "text":
            text = (ev["message"].get("text") or "").strip()
            low = text.lower()
            # /save 7203.T
            if low.startswith("/save"):
                parts = text.split()
                t = parts[-1] if len(parts) > 1 else ""
                if t:
                    _save_action(user, t, "save_order", "from_line_text")
            elif low.startswith("/reject"):
                parts = text.split()
                t = parts[-1] if len(parts) > 1 else ""
                if t:
                    _save_action(user, t, "reject", "from_line_text")
            elif low.startswith("/snooze"):
                # /snooze 7203.T 120
                parts = text.split()
                t = parts[1] if len(parts) > 1 else ""
                mins = int(parts[2]) if len(parts) > 2 else 120
                if t:
                    until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                    _save_action(user, t, "notify", f"snooze_until={until.isoformat()}")
            continue

    return _ok()