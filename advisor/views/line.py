from __future__ import annotations
import json, os
from typing import Dict
from urllib.parse import parse_qs

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import get_user_model

from advisor.models import ActionLog

# 署名検証は必要なら後で追加（まずは最短で通す）
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

def _act(user, ticker: str, action: str, note: str = ""):
    """ActionLogに1行だけ記録（postbackの受け口）"""
    ActionLog.objects.create(user=user, ticker=ticker.upper(), action=action, note=note or "line-postback")

@csrf_exempt
def webhook(request: HttpRequest) -> HttpResponse:
    """LINE Messaging API のWebhook（postbackボタン受信）"""
    if request.method != "POST":
        return JsonResponse({"ok": True})

    try:
        body = request.body.decode("utf-8")
        data = json.loads(body)
    except Exception:
        return JsonResponse({"ok": False, "err": "bad json"}, status=400)

    User = get_user_model()
    user = User.objects.first()

    for ev in data.get("events", []):
        if ev.get("type") == "postback":
            raw = ev.get("postback", {}).get("data", "")
            q: Dict[str, str] = {k: v[0] for k, v in parse_qs(raw).items()}
            action = q.get("action")
            ticker = q.get("ticker")
            if user and action and ticker:
                if action == "save":
                    _act(user, ticker, "save_order")
                elif action == "remind2h":
                    _act(user, ticker, "remind_later", "2h")
                elif action == "reject":
                    _act(user, ticker, "reject")

    return JsonResponse({"ok": True})