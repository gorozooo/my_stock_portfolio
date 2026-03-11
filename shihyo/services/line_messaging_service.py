"""
[FILE] line_messaging_service.py
[PATH] <project_root>/shihyo/services/line_messaging_service.py

このファイルは何？
- LINE Messaging API へ push message を送るだけの配送サービスです。
- DB参照や Flex 組み立ては持ちません。
"""

from __future__ import annotations

from typing import Any

import requests


def push_flex_message(
    channel_access_token: str,
    to_user_id: str,
    alt_text: str,
    flex_contents: dict[str, Any],
) -> tuple[bool, int, str]:
    headers = {
        "Authorization": f"Bearer {channel_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": to_user_id,
        "messages": [
            {
                "type": "flex",
                "altText": alt_text,
                "contents": flex_contents,
            }
        ],
    }

    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers=headers,
        json=payload,
        timeout=15,
    )
    return 200 <= r.status_code < 300, r.status_code, r.text