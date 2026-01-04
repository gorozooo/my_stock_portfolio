# -*- coding: utf-8 -*-
import json, logging, os, fcntl
from typing import Optional, Tuple
from urllib.parse import parse_qsl
from datetime import datetime, timezone

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from portfolio.models_line import LineContact
from portfolio.services.line_api import verify_signature, reply

logger = logging.getLogger(__name__)

# 環境変数で初回だけ挨拶（1 のときのみ）
WELCOME_ONCE = os.getenv("LINE_WELCOME_ONCE", "").strip() == "1"

# ---------- 共通ユーティリティ ----------
def _media_root() -> str:
    try:
        from django.conf import settings
        mr = getattr(settings, "MEDIA_ROOT", "")
        return mr or os.path.join(os.getcwd(), "media")
    except Exception:
        return os.path.join(os.getcwd(), "media")

def _feedback_path() -> str:
    # advisor に依存しない保存先へ変更
    return os.path.join(_media_root(), "line", "feedback.jsonl")

def _comment_history_path(user_id: str) -> str:
    # advisor に依存しない保存先へ変更
    return os.path.join(_media_root(), "line", f"comment_history_{user_id}.jsonl")

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

# ---------- JSONL 追記（排他付き） ----------
def _append_jsonl(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(line)
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass

# ---------- 直近コメント（本文/モード）補完 ----------
def _last_comment_for(user_id: str) -> Tuple[Optional[str], Optional[str]]:
    path = _comment_history_path(user_id)
    if not os.path.exists(path):
        return (None, None)
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = 4096
            buf = b""
            while size > 0 and b"\n" not in buf:
                step = min(chunk, size)
                size -= step
                f.seek(size)
                buf = f.read(step) + buf
            line = buf.strip().split(b"\n")[-1].decode("utf-8")
        obj = json.loads(line)
        text = (obj.get("text") or "").strip() if isinstance(obj, dict) else ""
        mode = (obj.get("mode") or "generic").strip().lower() if isinstance(obj, dict) else "generic"
        if mode not in ("preopen", "postopen", "noon", "afternoon", "outlook"):
            mode = "generic"
        return (text or None, mode or None)
    except Exception as e:
        logger.debug("last_comment parse error: %s", e)
        return (None, None)

# ---------- “feedback” 抽出ヘルパ ----------
def _parse_feedback_from_text(s: str) -> dict | None:
    if not isinstance(s, str):
        return None
    t = s.strip()
    low = t.lower().replace("：", ":").replace("；", ";")
    if not (low.startswith("feedback;") or low.startswith("feedback:") or low.startswith("feedback ")):
        return None

    arg = ""
    for sep in (";", ":", " "):
        if sep in low:
            parts = low.split(sep, 1)
            if len(parts) == 2:
                arg = parts[1].strip()
                break

    if arg in ("+1", "up", "👍", "good", "like", "ok"):
        return {"choice": "up"}
    if arg in ("-1", "down", "👎", "bad", "ng", "no"):
        return {"choice": "down"}
    if arg in ("edit", "fix", "✏️", "修正"):
        return {"choice": "edit"}
    return {"choice": arg or "unknown"}

def _parse_feedback_from_postback(data: str) -> dict | None:
    if not isinstance(data, str) or not data:
        return None
    qs = dict(parse_qsl(data, keep_blank_values=True))
    t = (qs.get("type") or qs.get("t") or "").lower()
    if t not in ("feedback", "fb") and not any(k in qs for k in ("choice", "c")):
        return None

    choice = (qs.get("choice") or qs.get("c") or "").strip()
    mode   = (qs.get("mode")   or qs.get("m") or "").strip().lower()
    text   = (qs.get("text")   or qs.get("x") or "").strip() or None

    if choice in ("+1", "up", "good", "like", "ok", "👍"):
        choice = "up"
    elif choice in ("-1", "down", "bad", "ng", "no", "👎"):
        choice = "down"
    elif choice in ("edit", "fix", "✏️", "修正"):
        choice = "edit"

    if mode not in ("preopen", "postopen", "noon", "afternoon", "outlook"):
        mode = "generic"

    return {"choice": choice, "mode": mode, "text": text}

# ---------- Webhook 本体 ----------
@csrf_exempt
def line_webhook(request):
    """
    LINE Webhook（portfolio側のみで完結）
      - userId を upsert 保存
      - 『id』だけは返信で userId を返す
      - 友だち追加 follow は既定サイレント（LINE_WELCOME_ONCE=1 かつ初回のみ挨拶）
      - feedback（message / postback）を JSONL へ保存
    """
    if request.method != "POST":
        return HttpResponse("OK")

    # 開発用バイパス（?bypass=1）
    if not (
        request.GET.get("bypass") == "1"
        or verify_signature(request.body, request.headers.get("X-Line-Signature", ""))
    ):
        logger.warning("LINE signature mismatch")
        return HttpResponse(status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        logger.exception("LINE payload parse error")
        return HttpResponse(status=400)

    for ev in payload.get("events", []):
        etype = ev.get("type")
        src = ev.get("source") or {}
        user_id = src.get("userId")
        reply_token = ev.get("replyToken")

        if not user_id:
            continue

        # upsert
        _, created = LineContact.objects.get_or_create(user_id=user_id, defaults={})

        # ---- follow（友だち追加）----
        if etype == "follow":
            if WELCOME_ONCE and created and reply_token:
                reply(reply_token, "登録ありがとう！あなたのIDを保存しました ✅\n「id」と送るとIDを返信します。")
            continue

        # ---- postback（ボタン押下）----
        if etype == "postback":
            pb = ev.get("postback") or {}
            data = (pb.get("data") or "").strip()

            fb = _parse_feedback_from_postback(data)
            if fb:
                txt = fb.get("text")
                md  = fb.get("mode")
                if not txt or not md or md == "generic":
                    last_text, last_mode = _last_comment_for(user_id)
                    if not txt:
                        txt = last_text
                    if not md or md == "generic":
                        md = last_mode or "generic"

                row = {
                    "ts": _now_iso(),
                    "user": user_id,
                    "mode": md or "generic",
                    "text": txt,
                    "choice": fb.get("choice"),
                    "via": "postback",
                }
                _append_jsonl(_feedback_path(), row)
                logger.info("saved feedback(postback): %s", row)
            else:
                logger.debug("postback(no-feedback): %s", data)
            continue

        # ---- message（テキスト）----
        if etype == "message":
            msg = ev.get("message") or {}
            if msg.get("type") == "text":
                text_raw = (msg.get("text") or "").strip()
                low = text_raw.lower()

                # a) ID 返信
                if low == "id" and reply_token:
                    reply(reply_token, f"あなたのLINE ID:\n{user_id}")
                    continue

                # b) feedback; ... を保存
                fb = _parse_feedback_from_text(text_raw)
                if fb:
                    txt = fb.get("text")
                    md  = fb.get("mode")
                    if not txt or not md or md == "generic":
                        last_text, last_mode = _last_comment_for(user_id)
                        if not txt:
                            txt = last_text
                        if not md or md == "generic":
                            md = last_mode or "generic"

                    row = {
                        "ts": _now_iso(),
                        "user": user_id,
                        "mode": md or "generic",
                        "text": txt,
                        "choice": fb.get("choice"),
                        "via": "message",
                    }
                    _append_jsonl(_feedback_path(), row)
                    logger.info("saved feedback(message): %s", row)
                    continue

                # c) ヘルプ（portfolio側だけに合わせて簡素化）
                if reply_token:
                    reply(reply_token, "コマンド: id / feedback; +1 / feedback; -1 / feedback; edit")
            continue

        # ---- その他イベントはサイレント ----
        logger.debug("LINE event(silent): type=%s user=%s", etype, user_id)

    return HttpResponse("OK")
