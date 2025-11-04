import os
import requests
from typing import List, Dict

LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')

def _post_message(payload: Dict):
    token = LINE_CHANNEL_ACCESS_TOKEN
    if not token:
        return False, 'LINE_CHANNEL_ACCESS_TOKEN is not set'

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        return (r.status_code == 200), r.text
    except Exception as e:
        return False, str(e)


def send_ops_alert(title: str, lines: List[str]):
    """
    運用サマリをMessaging APIで送信。
    注意：userIdまたはgroupIdを環境変数LINE_USER_IDで指定。
    """
    user_id = os.getenv('LINE_USER_ID')
    if not user_id:
        return False, 'LINE_USER_ID is not set'

    text = f"[{title}]\n" + "\n".join(lines[:25])
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}]
    }

    ok, info = _post_message(payload)
    return ok, info