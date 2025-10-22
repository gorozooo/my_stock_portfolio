# -*- coding: utf-8 -*-
import os, json, logging, io, fcntl
from typing import Optional, Tuple
from urllib.parse import parse_qsl
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from portfolio.models_line import LineContact
from portfolio.services.line_api import verify_signature, reply

logger = logging.getLogger(__name__)

# 環境変数で初回だけ挨拶（1 のときのみ）
WELCOME_ONCE = os.getenv("LINE_WELCOME_ONCE", "").strip() == "1"

# ---------- 共通ユーティリティ ----------
def _media_root() -> str:
    # settings.MEDIA_ROOT が未設定でも media/ を使えるように
    try:
        from django.conf import settings
        mr = getattr(settings, "MEDIA_ROOT", "")
        return mr or os.path.join(os.getcwd(), "media")
    except Exception:
        return os.path.join(os.getcwd(), "media")

def _feedback_path() -> str:
    return os.path.join(_media_root(), "advisor", "feedback.jsonl")

def _comment_history_path(user_id: str) -> str:
    # 新パス（media/advisor/...）優先、無ければ旧互換（プロジェクト直下/advisor/...）
    p_new = os.path.join(_media_root(), "advisor", f"comment_history_{user_id}.jsonl")
    if os.path.exists(p_new):
        return p_new
    p_old = os.path.join(os.getcwd(), "advisor", f"comment_history_{user_id}.jsonl")
    return p_old

def _now_iso() -> str:
    from datetime import datetime, timezone
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
    """
    ユーザー別の直近コメント本文とモードを返す。
    期待フォーマット: 1行=JSON { "mode": "...", "text": "..." }
    """
    path = _comment_history_path(user_id)
    if not os.path.exists(path):
        return (None, None)
    try:
        # 最後の1行だけ効率良く読む
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
    """
    テキストから feedback コマンドを抽出。
    例: 'feedback; +1', 'feedback; -1', 'feedback; edit', 'feedback:+1'
    """
    if not isinstance(s, str):
        return None
    t = s.strip()
    low = t.lower().replace("：", ":").replace("；", ";")
    if not (low.startswith("feedback;") or low.startswith("feedback:") or low.startswith("feedback ")):
        return None

    # 区切り後ろを取り出して整形
    arg = ""
    for sep in (";", ":", " "):
        if sep in low:
            parts = low.split(sep, 1)
            if len(parts) == 2:
                arg = parts[1].strip()
                break

    # 記号の揺れ対応
    if arg in ("+1", "up", "👍", "good", "like", "ok"):
        return {"choice": "up"}
    if arg in ("-1", "down", "👎", "bad", "ng", "no"):
        return {"choice": "down"}
    if arg in ("edit", "fix", "✏️", "修正"):
        return {"choice": "edit"}

    return {"choice": arg or "unknown"}

def _parse_feedback_from_postback(data: str) -> dict | None:
    """
    Postback の data を解析。
    期待例:
      type=feedback&choice=up&mode=noon
      t=fb&c=-1&m=afternoon
    """
    if not isinstance(data, str) or not data:
        return None
    qs = dict(parse_qsl(data, keep_blank_values=True))
    # 明示 type が無い実装にも対応
    t = (qs.get("type") or qs.get("t") or "").lower()
    if t not in ("feedback", "fb") and not any(k in qs for k in ("choice", "c")):
        return None

    choice = (qs.get("choice") or qs.get("c") or "").strip()
    mode   = (qs.get("mode")   or qs.get("m") or "").strip().lower()
    text   = (qs.get("text")   or qs.get("x") or "").strip() or None

    # 記号の正規化
    if choice in ("+1", "up", "good", "like", "ok", "👍"):
        choice = "up"
    elif choice in ("-1", "down", "bad", "ng", "no", "👎"):
        choice = "down"
    elif choice in ("edit", "fix", "✏️", "修正"):
        choice = "edit"
    if not choice:
        return None

    if mode not in ("preopen","postopen","noon","afternoon","outlook"):
        mode = "generic"

    return {"choice": choice, "mode": mode, "text": text}

# ---------- Webhook 本体 ----------
@csrf_exempt
def line_webhook(request):
    """
    LINE Webhook（サイレント運用）
      - userId を upsert 保存
      - 『id』だけは返信で userId を返す
      - 友だち追加 follow はデフォルト無返信（LINE_WELCOME_ONCE=1 かつ初回のみ挨拶）
      - ボタン(Postback) / テキストどちらの feedback も advisor/feedback.jsonl に保存
        → text/mode が欠けている場合は直近カードから自動補完
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

        # upsert（初回判定に使う）
        _, created = LineContact.objects.get_or_create(user_id=user_id, defaults={})

        # ---- follow（友だち追加）----
        if etype == "follow":
            if WELCOME_ONCE and created:
                rtoken = ev.get("replyToken")
                if rtoken:
                    reply(rtoken, "登録ありがとう！あなたのIDを保存しました ✅\n「id」と送るとIDを返信します。")
            continue  # 既定はサイレント

        # ---- message（テキスト）----
        if etype == "message":
            msg = ev.get("message") or {}
            if msg.get("type") == "text":
                text_raw = (msg.get("text") or "").strip()
                low = text_raw.lower()

                # a) ID 返信
                if low == "id":
                    rtoken = ev.get("replyToken")
                    if rtoken:
                        reply(rtoken, f"あなたのLINE ID:\n{user_id}")
                    continue

                # b) feedback; ... を保存（不足は直近カードで補完）
                fb = _parse_feedback_from_text(text_raw)
                if fb:
                    txt = fb.get("text")
                    md  = fb.get("mode")
                    if not txt or not md or md == "generic":
                        last_text, last_mode = _last_comment_for(user_id)
                        if not txt: txt = last_text
                        if not md or md == "generic": md = last_mode or "generic"
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

                # c) それ以外はサイレント
                logger.debug("LINE message(silent): %s", text_raw)
            continue  # 他の message 種別は無視

        # ---- postback（ボタン押下）----
        if etype == "postback":
            pb = ev.get("postback") or {}
            data = pb.get("data") or ""
            fb = _parse_feedback_from_postback(data)
            if fb:
                txt = fb.get("text")
                md  = fb.get("mode")
                if not txt or not md or md == "generic":
                    last_text, last_mode = _last_comment_for(user_id)
                    if not txt: txt = last_text
                    if not md or md == "generic": md = last_mode or "generic"
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

        # ---- その他イベントはサイレント ----
        logger.debug("LINE event(silent): type=%s user=%s", etype, user_id)

    return HttpResponse("OK")