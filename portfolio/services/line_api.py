# -*- coding: utf-8 -*-
import hmac, hashlib, base64, json, logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.line.me/v2/bot"

def _auth_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.LINE_CHANNEL_ACCESS_TOKEN}",
    }

def verify_signature(body_bytes: bytes, x_line_signature: str) -> bool:
    """LINE 署名検証（必須）"""
    key = settings.LINE_CHANNEL_SECRET.encode("utf-8")
    mac = hmac.new(key, body_bytes, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, x_line_signature or "")

def reply(reply_token: str, message_text: str):
    url = f"{API_BASE}/message/reply"
    payload = {"replyToken": reply_token, "messages": [{"type":"text","text":message_text}]}
    r = requests.post(url, headers=_auth_headers(), json=payload, timeout=10)
    logger.info("LINE reply %s %s", r.status_code, r.text[:200])
    return r

def push(to_user_id: str, message_text: str):
    url = f"{API_BASE}/message/push"
    payload = {"to": to_user_id, "messages": [{"type":"text","text":message_text}]}
    r = requests.post(url, headers=_auth_headers(), json=payload, timeout=10)
    logger.info("LINE push %s %s", r.status_code, r.text[:200])
    return r