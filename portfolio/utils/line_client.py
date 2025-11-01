# portfolio/utils/line_client.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, hmac, hashlib, base64, requests
from typing import Dict, Any, Optional
from django.conf import settings

API_BASE = "https://api.line.me/v2/bot"

def _token() -> str:
    # settings優先 → 環境変数フォールバック
    return getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))

def _secret() -> Optional[str]:
    return getattr(settings, "LINE_CHANNEL_SECRET", os.getenv("LINE_CHANNEL_SECRET"))

def _auth_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_token()}",
    }

def verify_signature(body_bytes: bytes, x_line_signature: str) -> bool:
    secret = _secret()
    if not secret:
        # 開発時は未設定でも通す（本番は必ず設定推奨）
        return True
    mac = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, x_line_signature or "")

def reply_text(reply_token: str, message_text: str) -> requests.Response:
    url = f"{API_BASE}/message/reply"
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": message_text}]}
    return requests.post(url, headers=_auth_headers(), data=json.dumps(payload), timeout=10)

def push_text(to_user_id: str, message_text: str) -> requests.Response:
    url = f"{API_BASE}/message/push"
    payload = {"to": to_user_id, "messages": [{"type": "text", "text": message_text}]}
    return requests.post(url, headers=_auth_headers(), data=json.dumps(payload), timeout=10)

def push_flex(to_user_id: str, *, alt_text: str, contents: Dict[str, Any], quick_reply: bool = True) -> requests.Response:
    """
    contents: Flexの bubble / carousel を想定
    """
    url = f"{API_BASE}/message/push"
    msg: Dict[str, Any] = {"type": "flex", "altText": alt_text, "contents": contents}
    if quick_reply:
        msg["quickReply"] = {
            "items": [
                {"type": "action", "action": {"type": "message", "label": "👍 いいね", "text": "feedback:+1"}},
                {"type": "action", "action": {"type": "message", "label": "👎 いまいち", "text": "feedback:-1"}},
                {"type": "action", "action": {"type": "message", "label": "✏️ 修正", "text": "feedback:edit"}},
            ]
        }
    payload = {"to": to_user_id, "messages": [msg]}
    return requests.post(url, headers=_auth_headers(), data=json.dumps(payload, ensure_ascii=False), timeout=10)