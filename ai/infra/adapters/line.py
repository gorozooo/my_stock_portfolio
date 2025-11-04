import os
import requests
from typing import List

LINE_NOTIFY_TOKEN = os.getenv('LINE_NOTIFY_TOKEN')  # 環境変数に入れておく

def _post_notify(message: str):
    token = LINE_NOTIFY_TOKEN
    if not token:
        return False, 'LINE_NOTIFY_TOKEN is not set'
    url = "https://notify-api.line.me/api/notify"
    headers = {"Authorization": f"Bearer {token}"}
    data = {"message": message}
    try:
        r = requests.post(url, headers=headers, data=data, timeout=10)
        return (r.status_code == 200), (r.text if r.text else r.status_code)
    except Exception as e:
        return False, str(e)

def send_ops_alert(title: str, lines: List[str]):
    """
    運用向けサマリ通知。メッセージはテキストのみ（安定重視）。
    """
    msg = f"[{title}]\n" + "\n".join(lines[:25])  # 長すぎ対策
    ok, info = _post_notify(msg)
    return ok, info