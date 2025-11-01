# -*- coding: utf-8 -*-
import hmac, hashlib, base64, json, logging, os
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.line.me/v2/bot"

# ---- トークン/シークレットを robust に取得 ----
def _get_token() -> str:
    return getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", None) or os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

def _get_secret() -> str:
    return getattr(settings, "LINE_CHANNEL_SECRET", None) or os.getenv("LINE_CHANNEL_SECRET", "")

def _auth_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_get_token()}",
    }

# ------------------------------------------------------------------
# 署名検証
# ------------------------------------------------------------------
def verify_signature(body_bytes: bytes, x_line_signature: str) -> bool:
    secret = _get_secret()
    if not secret:
        # 開発環境などで未設定なら true（上位で制御）
        return True
    mac = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, x_line_signature or "")

# ------------------------------------------------------------------
# 返信（replyTokenを使う即時返信）
# ------------------------------------------------------------------
def reply(reply_token: str, message_text: str):
    url = f"{API_BASE}/message/reply"
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": message_text}],
    }
    r = requests.post(url, headers=_auth_headers(), json=payload, timeout=10)
    logger.info("LINE reply %s %s", r.status_code, r.text[:200])
    return r

# ------------------------------------------------------------------
# プッシュ（テキスト）
# ------------------------------------------------------------------
def push_text(to_user_id: str, message_text: str):
    url = f"{API_BASE}/message/push"
    payload = {
        "to": to_user_id,
        "messages": [{"type": "text", "text": message_text}],
    }
    r = requests.post(url, headers=_auth_headers(), json=payload, timeout=10)
    logger.info("LINE push_text %s %s", r.status_code, r.text[:200])
    return r

# ------------------------------------------------------------------
# プッシュ（Flex + クイックリプライ任意）
#   呼び出し想定: push_flex(uid, alt_text, contents)  ← advisor_daily_brief 等
# ------------------------------------------------------------------
def push_flex(to_user_id: str, alt_text: str, contents: dict, quick_reply: bool = True):
    url = f"{API_BASE}/message/push"
    msg = {
        "type": "flex",
        "altText": alt_text,
        "contents": contents,   # bubble or carousel
    }
    if quick_reply:
        msg["quickReply"] = {
            "items": [
                {"type": "action", "action": {"type": "message", "label": "👍 いいね", "text": "feedback:+1"}},
                {"type": "action", "action": {"type": "message", "label": "👎 いまいち", "text": "feedback:-1"}},
                {"type": "action", "action": {"type": "message", "label": "✏️ 修正", "text": "feedback:edit"}},
            ]
        }

    payload = {"to": to_user_id, "messages": [msg]}
    r = requests.post(url, headers=_auth_headers(), json=payload, timeout=10)
    logger.info("LINE push_flex %s %s", r.status_code, r.text[:200])
    return r