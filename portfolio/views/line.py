# -*- coding: utf-8 -*-
import os
import json
import logging
from datetime import datetime
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from portfolio.models_line import LineContact
from portfolio.services.line_api import verify_signature, reply

logger = logging.getLogger(__name__)

# 環境変数で初回だけ挨拶を出したい場合は 1 を設定（未設定/その他はサイレント）
WELCOME_ONCE = os.getenv("LINE_WELCOME_ONCE", "").strip() == "1"

# 追記先ファイル（統一パス）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_PATH = os.path.join(BASE_DIR, "media", "advisor", "feedback.jsonl")
LOCK_PATH = os.path.join(BASE_DIR, "media", "advisor", "feedback.lock")


def append_feedback_line(mode: str, choice: str, text: str, edited_text: str = "", tags=None):
    """feedback.jsonl に安全に追記"""
    os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "mode": mode or "generic",
        "choice": choice,
        "text": text,
    }
    if edited_text:
        rec["edited_text"] = edited_text
    if tags:
        rec["tags"] = tags

    # flockで排他制御して追記（atomic append）
    import fcntl
    try:
        with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
        logger.info("[feedback] appended to %s: %s", FEEDBACK_PATH, rec)
    except Exception as e:
        logger.exception("[feedback] append failed: %s", e)


@csrf_exempt
def line_webhook(request):
    """
    LINE Webhook 受信（サイレント版）
      - userId を保存（upsert）
      - feedback;edit;down;up のようなコマンドを受けたら advisor/feedback.jsonl に記録
      - 既定は「返信しない」。例外として「id」だけ自分の userId を返す
      - follow（友だち追加）時は環境変数 LINE_WELCOME_ONCE=1 のときだけ初回挨拶
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

        # ---- userID登録/upsert ----
        obj, created = LineContact.objects.get_or_create(user_id=user_id, defaults={})

        # ---- 友だち追加 ----
        if etype == "follow":
            if WELCOME_ONCE and created:
                rtoken = ev.get("replyToken")
                if rtoken:
                    reply(rtoken, "登録ありがとう！あなたのIDを保存しました ✅\n「id」と送るとIDを返信します。")
            continue

        # ---- テキストメッセージ ----
        if etype == "message":
            msg = ev.get("message") or {}
            if msg.get("type") != "text":
                continue
            text_raw = (msg.get("text") or "").strip()
            text = text_raw.lower()
            rtoken = ev.get("replyToken")

            # a) ID要求
            if text == "id" and rtoken:
                reply(rtoken, f"あなたのLINE ID:\n{user_id}")
                continue

            # b) feedback コマンド形式
            # 例: feedback;noon;up;🔥買いが優勢…
            #     edit;noon;✏️;🌤拮抗…;🌤拮抗、短期は回転重視。
            if text.startswith(("feedback;", "edit;", "up;", "down;")):
                parts = text_raw.split(";", 4)
                choice = parts[0]
                mode = parts[1] if len(parts) > 1 else "generic"
                sub_choice = parts[2] if len(parts) > 2 else ""
                txt = parts[3] if len(parts) > 3 else ""
                edited = parts[4] if len(parts) > 4 else ""
                append_feedback_line(mode, choice or sub_choice, txt, edited)
                logger.info("LINE feedback recorded from %s: %s", user_id, text_raw)
                # 「登録ありがとう」などは返信しない
                continue

            # c) それ以外はサイレント
            logger.debug("LINE message (silent): from=%s text=%s", user_id, text_raw)
            continue

        # ---- その他イベント ----
        logger.debug("LINE event (silent): type=%s user=%s", etype, user_id)

    return HttpResponse("OK")