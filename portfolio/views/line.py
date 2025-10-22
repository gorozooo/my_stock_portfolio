# -*- coding: utf-8 -*-
import os, json, logging, re
from datetime import datetime, timezone
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from portfolio.models_line import LineContact
from portfolio.services.line_api import verify_signature, reply

logger = logging.getLogger(__name__)

WELCOME_ONCE = os.getenv("LINE_WELCOME_ONCE", "").strip() == "1"

# ===== 保存ユーティリティ =====
def _advisor_dir() -> str:
    base = os.path.join(os.getcwd(), "media", "advisor")
    os.makedirs(base, exist_ok=True)
    return base

def _feedback_path() -> str:
    return os.path.join(_advisor_dir(), "feedback.jsonl")

def _append_jsonl(path: str, row: dict) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("feedback append failed")

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

# 軽いパーサ（feedback; +1 / -1 / edit / good / bad / 👍 / 👎）
_FB_RE = re.compile(r"^\s*feedback\s*[:;]\s*(.+)$", re.I)

def _parse_feedback_text(text_raw: str) -> dict | None:
    m = _FB_RE.match(text_raw or "")
    if not m:
        return None
    val = m.group(1).strip().lower()
    # 記号や別名を吸収
    mapping = {
        "+1": "up", "good": "up", "👍": "up", "like": "up", "ok": "up",
        "-1": "down", "bad": "down", "👎": "down", "ng": "down", "no": "down",
        "edit": "edit", "fix": "edit", "✏️": "edit", "修正": "edit",
    }
    choice = mapping.get(val, val)
    return {"choice": choice}

@csrf_exempt
def line_webhook(request):
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

        # upsert
        LineContact.objects.update_or_create(user_id=user_id, defaults={})

        # ---- follow: 既定サイレント、必要なら初回だけ挨拶 ----
        if etype == "follow":
            if WELCOME_ONCE:
                rtoken = ev.get("replyToken")
                if rtoken:
                    reply(rtoken, "登録ありがとう！あなたのIDを保存しました ✅\n「id」と送るとIDを返信します。")
            continue

        # ---- postback（将来のボタン用。data に JSON or key=value を想定）----
        if etype == "postback":
            data = ev.get("postback", {}).get("data") or ""
            rec = None
            # JSON優先
            try:
                d = json.loads(data)
                if isinstance(d, dict) and d.get("k") == "fb":
                    rec = {
                        "choice": d.get("choice"),
                        "mode": d.get("mode") or "generic",
                        "text": (d.get("text") or "").strip() or None,
                    }
            except Exception:
                # key=value 形式: k=fb&choice=up&mode=noon
                kv = dict(x.split("=", 1) for x in data.split("&") if "=" in x)
                if kv.get("k") == "fb":
                    rec = {
                        "choice": kv.get("choice"),
                        "mode": kv.get("mode") or "generic",
                        "text": kv.get("text"),
                    }
            if rec and rec.get("choice"):
                _append_jsonl(_feedback_path(), {
                    "ts": _now_iso(),
                    "user": user_id,
                    **rec
                })
            continue

        # ---- message（テキスト）----
        if etype == "message":
            msg = ev.get("message") or {}
            if msg.get("type") != "text":
                continue
            text_raw = (msg.get("text") or "").strip()

            # a) id だけは返信
            if text_raw.lower() == "id":
                rtoken = ev.get("replyToken")
                if rtoken:
                    reply(rtoken, f"あなたのLINE ID:\n{user_id}")
                continue

            # b) feedback; … / edit; … をファイル保存（サイレント）
            fb = _parse_feedback_text(text_raw)
            if fb:
                _append_jsonl(_feedback_path(), {
                    "ts": _now_iso(),
                    "user": user_id,
                    "mode": "generic",     # ← 現状は不明。postback対応にすると埋まります
                    "text": None,          # ← 同上
                    **fb
                })
                continue

            # c) 完全サイレント
            logger.debug("LINE message (silent): %s", text_raw)
            continue

        # その他は無視
    return HttpResponse("OK")