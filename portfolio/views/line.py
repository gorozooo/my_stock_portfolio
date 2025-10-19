# -*- coding: utf-8 -*-
import json, logging
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from portfolio.models_line import LineContact
from portfolio.services.line_api import verify_signature, reply

logger = logging.getLogger(__name__)

@csrf_exempt
def line_webhook(request):
    """
    LINE Webhook 受信:
      - userId を保存（upsert）
      - 最初のメッセージ or follow に返信
      - 「id」と送ると自分の userId を返す
    """
    if request.method != "POST":
        return HttpResponse("OK")

    body = request.body
    sig = request.headers.get("X-Line-Signature", "")
    if not verify_signature(body, sig):
        logger.warning("LINE signature mismatch")
        return HttpResponse(status=403)

    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:
        return HttpResponse(status=400)

    for ev in obj.get("events", []):
        etype = ev.get("type")
        src = ev.get("source") or {}
        user_id = src.get("userId")

        if user_id:
            LineContact.objects.update_or_create(user_id=user_id, defaults={})

        if etype in ("follow", "message"):
            rtoken = ev.get("replyToken")
            if not rtoken:
                continue

            text = "登録ありがとう！あなたのIDを保存しました ✅\n「id」と送るとIDを返信します。"
            if etype == "message":
                msg = ev.get("message") or {}
                if msg.get("type") == "text" and msg.get("text","").strip().lower() == "id":
                    text = f"あなたのLINE ID:\n{user_id}"

            reply(rtoken, text)

    return HttpResponse("OK")