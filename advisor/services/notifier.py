from __future__ import annotations
import os
from typing import List, Optional
from django.conf import settings

# LINE SDK（Messaging API）
from linebot import LineBotApi
from linebot.models import TextSendMessage, QuickReply, QuickReplyButton, MessageAction

def _line() -> Optional[LineBotApi]:
    token = getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "") or os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        return None
    return LineBotApi(token)

def send_line_text(text: str, *, quick_actions: Optional[List[dict]] = None) -> bool:
    """
    単一ユーザーへテキスト送信。quick_actionsは [{'label':'発注メモ','text':'/save 7203.T'}, ...] 形式
    """
    api = _line()
    if not api:
        return False
    user_id = getattr(settings, "LINE_USER_ID", "") or os.getenv("LINE_USER_ID", "")
    if not user_id:
        return False

    msg = TextSendMessage(
        text=text,
        quick_reply=(QuickReply(items=[
            QuickReplyButton(action=MessageAction(label=qa["label"], text=qa["text"]))
            for qa in (quick_actions or [])
        ]) if quick_actions else None)
    )
    api.push_message(user_id, messages=msg)
    return True