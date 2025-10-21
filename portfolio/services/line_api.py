# -*- coding: utf-8 -*-
import hmac, hashlib, base64, json, logging, os
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
    key = settings.LINE_CHANNEL_SECRET.encode("utf-8")
    mac = hmac.new(key, body_bytes, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, x_line_signature or "")

def reply(reply_token: str, message_text: str):
    url = f"{API_BASE}/message/reply"
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": message_text}]}
    r = requests.post(url, headers=_auth_headers(), json=payload, timeout=10)
    logger.info("LINE reply %s %s", r.status_code, r.text[:200])
    return r

def push(to_user_id: str, message_text: str):
    url = f"{API_BASE}/message/push"
    payload = {"to": to_user_id, "messages": [{"type": "text", "text": message_text}]}
    r = requests.post(url, headers=_auth_headers(), json=payload, timeout=10)
    logger.info("LINE push %s %s", r.status_code, r.text[:200])
    return r


# --- Flex対応＋クイックリプライ付き ---
LINE_API = "https://api.line.me/v2/bot/message/push"
TOKEN = getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))

def push(to: str, text: str):
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    payload = {"to": to, "messages": [{"type": "text", "text": text}]}
    return requests.post(LINE_API, headers=headers, data=json.dumps(payload), timeout=10)


def push_flex(to: str, alt_text: str, contents: dict, quick_reply: bool = True):
    """Flex Message（bubble or carousel）＋クイックリプライ"""
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    msg = {
        "type": "flex",
        "altText": alt_text,
        "contents": contents,
    }

    if quick_reply:
        msg["quickReply"] = {
            "items": [
                {"type": "action", "action": {"type": "message", "label": "👍 いいね", "text": "feedback:+1"}},
                {"type": "action", "action": {"type": "message", "label": "👎 いまいち", "text": "feedback:-1"}},
                {"type": "action", "action": {"type": "message", "label": "✏️ 修正", "text": "feedback:edit"}},
            ]
        }

    payload = {"to": to, "messages": [msg]}
    return requests.post(LINE_API, headers=headers, data=json.dumps(payload, ensure_ascii=False), timeout=10)