# =========================================================
# [FILE] line_push_service.py
# [PATH] <project_root>/tradeai/services/notify/line_push_service.py
#
# このファイルは何？
# - LINE Messaging API を使って通知を送るサービスです。
# - テキスト通知だけでなく、Flex Message も送れるようにしています。
# =========================================================

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings


LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def _post_line_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    token = (getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "") or "").strip()
    user_id = (getattr(settings, "LINE_USER_ID", "") or "").strip()

    if not token:
        return False, "LINE_CHANNEL_ACCESS_TOKEN が未設定です。"
    if not user_id:
        return False, "LINE_USER_ID が未設定です。"

    req = urllib.request.Request(
        LINE_PUSH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8", errors="ignore")
            return True, body or "ok"
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = str(exc)
        return False, f"HTTP {exc.code}: {body}"
    except Exception as exc:
        return False, str(exc)


def send_line_messages(messages: list[dict[str, Any]]) -> tuple[bool, str]:
    safe_messages = [m for m in messages if isinstance(m, dict)]
    if not safe_messages:
        return False, "送信メッセージが空です。"

    user_id = (getattr(settings, "LINE_USER_ID", "") or "").strip()
    if not user_id:
        return False, "LINE_USER_ID が未設定です。"

    payload = {
        "to": user_id,
        "messages": safe_messages[:5],
    }
    return _post_line_payload(payload)


def send_line_text(message: str) -> tuple[bool, str]:
    safe_text = (message or "").strip()
    if not safe_text:
        return False, "送信メッセージが空です。"

    return send_line_messages(
        [
            {
                "type": "text",
                "text": safe_text[:4500],
            }
        ]
    )


def send_line_flex(alt_text: str, contents: dict[str, Any]) -> tuple[bool, str]:
    safe_alt = (alt_text or "").strip()
    if not safe_alt:
        return False, "Flex の altText が空です。"
    if not isinstance(contents, dict):
        return False, "Flex contents が不正です。"

    return send_line_messages(
        [
            {
                "type": "flex",
                "altText": safe_alt[:400],
                "contents": contents,
            }
        ]
    )