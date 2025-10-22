# -*- coding: utf-8 -*-
import os
import json
import logging
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from portfolio.models_line import LineContact
from portfolio.services.line_api import verify_signature, reply

logger = logging.getLogger(__name__)

# 環境変数で初回だけ挨拶を出したい場合は 1 を設定（未設定/その他はサイレント）
WELCOME_ONCE = os.getenv("LINE_WELCOME_ONCE", "").strip() == "1"


@csrf_exempt
def line_webhook(request):
    """
    LINE Webhook 受信（サイレント版）
      - userId を保存（upsert）
      - 既定は「返信しない」。例外として「id」だけ自分の userId を返す
      - follow（友だち追加）時もサイレント（環境変数 LINE_WELCOME_ONCE=1 の場合のみ初回1回だけ挨拶）
      - feedback/edit など学習用コマンドは完全サイレントでログに記録
    """
    if request.method != "POST":
        return HttpResponse("OK")

    body = request.body
    sig = request.headers.get("X-Line-Signature", "")
    if not verify_signature(body, sig):
        logger.warning("LINE signature mismatch")
        return HttpResponse(status=403)

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        logger.exception("LINE payload parse error")
        return HttpResponse(status=400)

    for ev in payload.get("events", []):
        etype = ev.get("type")
        src = ev.get("source") or {}
        user_id = src.get("userId")

        if not user_id:
            continue

        # upsert しつつ、初回かどうかを判定
        obj, created = LineContact.objects.get_or_create(user_id=user_id, defaults={})
        if not created:
            # 既存なら更新だけ（タイムスタンプ等を持っていればここで更新）
            pass

        # ---- 返信制御 ----
        # 1) 友だち追加: 既定はサイレント。WELCOME_ONCE=1 のときのみ初回だけ挨拶を返す
        if etype == "follow":
            if WELCOME_ONCE and created:
                rtoken = ev.get("replyToken")
                if rtoken:
                    reply(rtoken, "登録ありがとう！あなたのIDを保存しました ✅\n「id」と送るとIDを返信します。")
            # 既定はサイレント
            continue

        # 2) テキストメッセージ
        if etype == "message":
            msg = ev.get("message") or {}
            if msg.get("type") != "text":
                continue
            text_raw = (msg.get("text") or "").strip()
            text = text_raw.lower()

            # a) ID 確認だけは返信する
            if text == "id":
                rtoken = ev.get("replyToken")
                if rtoken:
                    reply(rtoken, f"あなたのLINE ID:\n{user_id}")
                continue

            # b) 学習用コマンド（完全サイレント）
            if text.startswith("feedback;") or text.startswith("edit;"):
                logger.info("LINE feedback/edit received from %s: %s", user_id, text_raw)
                continue

            # c) それ以外はサイレント（以前の「登録ありがとう」を送らない）
            logger.debug("LINE message (silent): from=%s text=%s", user_id, text_raw)
            continue

        # 3) それ以外のイベントもサイレント
        logger.debug("LINE event (silent): type=%s user=%s", etype, user_id)

    return HttpResponse("OK")