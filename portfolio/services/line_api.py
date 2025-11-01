# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, hmac, hashlib, base64, logging
from typing import Dict, Any, Optional
import requests
from django.conf import settings

logger = logging.getLogger(__name__)
API_BASE = "https://api.line.me/v2/bot"

# =========================
# 内部ユーティリティ
# =========================
def _get_token() -> str:
    # settings 優先 → 環境変数フォールバック
    return getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))

def _get_secret() -> Optional[str]:
    return getattr(settings, "LINE_CHANNEL_SECRET", os.getenv("LINE_CHANNEL_SECRET"))

def _auth_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_get_token()}",
    }

# =========================
# 署名検証（未設定なら dev としてスキップ）
# =========================
def verify_signature(body_bytes: bytes, x_line_signature: str) -> bool:
    secret = _get_secret()
    if not secret:
        # 本番は必ず設定推奨。未設定時は開発用に検証スキップ。
        logger.warning("LINE_CHANNEL_SECRET not set; skipping signature verification.")
        return True
    mac = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, x_line_signature or "")

# =========================
# 返信（reply）
# =========================
def reply_text(reply_token: str, message_text: str) -> requests.Response:
    url = f"{API_BASE}/message/reply"
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": message_text}],
    }
    r = requests.post(url, headers=_auth_headers(),
                      data=json.dumps(payload, ensure_ascii=False), timeout=10)
    logger.info("LINE reply %s %s", r.status_code, r.text[:200])
    return r

# =========================
# プッシュ（push）
# =========================
def push_text(to_user_id: str, message_text: str) -> requests.Response:
    url = f"{API_BASE}/message/push"
    payload = {
        "to": to_user_id,
        "messages": [{"type": "text", "text": message_text}],
    }
    r = requests.post(url, headers=_auth_headers(),
                      data=json.dumps(payload, ensure_ascii=False), timeout=10)
    logger.info("LINE push_text %s %s", r.status_code, r.text[:200])
    return r

def push_flex(to_user_id: str, *, alt_text: str, contents: Dict[str, Any],
              quick_reply: bool = True) -> requests.Response:
    """
    contents: Flex の bubble / carousel を想定
    """
    url = f"{API_BASE}/message/push"
    msg: Dict[str, Any] = {
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
    payload = {"to": to_user_id, "messages": [msg]}
    r = requests.post(url, headers=_auth_headers(),
                      data=json.dumps(payload, ensure_ascii=False), timeout=10)
    logger.info("LINE push_flex %s %s", r.status_code, r.text[:200])
    return r

# 既存コード互換：以前の push() を使っていても動くように薄いラッパーを残す
def push(to_user_id: str, message_text: str) -> requests.Response:
    return push_text(to_user_id, message_text)

# =========================
# 簡易ヘルスチェック（任意）
# =========================
def health() -> Dict[str, Any]:
    return {
        "token_configured": bool(_get_token()),
        "secret_configured": bool(_get_secret()),
    }