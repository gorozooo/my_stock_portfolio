# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, hmac, hashlib, base64
from datetime import timedelta, timezone
import requests

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now as dj_now
from django.contrib.auth import get_user_model

from advisor.models import ActionLog
from advisor.models_order import OrderMemo
from advisor.models_trend import TrendResult

JST = timezone(timedelta(hours=9))

# ========== LINE API共通 ==========
def _line_token():
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

def _line_secret():
    return os.getenv("LINE_CHANNEL_SECRET")

def _verify_signature(request: HttpRequest) -> bool:
    secret = _line_secret()
    if not secret:
        return True
    sig = request.headers.get("X-Line-Signature", "")
    raw = request.body
    mac = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    calc = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(calc, sig)

def _reply_line(reply_token: str, text: str) -> None:
    token = _line_token()
    if not token or not reply_token:
        return
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    try:
        r = requests.post(url, headers=headers, data=json.dumps(body))
        print("[LINE reply]", r.status_code, r.text[:200])
    except Exception as e:
        print("[LINE reply error]", e)


# ========== 共通ユーティリティ ==========
def _actor():
    U = get_user_model()
    return U.objects.first()

def _ok():
    return JsonResponse({"ok": True})

def _save_action(user, ticker: str, action: str, note: str = ""):
    ActionLog.objects.create(user=user, ticker=ticker.upper(), action=action, note=note)


# ========== JPX銘柄名取得（補助） ==========
def _load_tse_map():
    base = os.getcwd()
    path = os.path.join(base, "data", "tse_list.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}

_TSE = _load_tse_map()

def _jpx_name(ticker: str, fallback: str | None = None) -> str:
    t = (ticker or "").upper()
    if t.endswith(".T"):
        t = t[:-2]
    v = _TSE.get(t) or {}
    nm = (v.get("name") or "").strip() if isinstance(v, dict) else ""
    return nm or (fallback or (ticker or ""))


# ========== Webhook本体 ==========
@csrf_exempt
def webhook(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse("OK")
    if not _verify_signature(request):
        return HttpResponse(status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponse(status=400)

    user = _actor()
    if not user:
        return _ok()

    events = payload.get("events") or []
    for ev in events:
        et = ev.get("type")
        reply_token = ev.get("replyToken", "")

        # ===== Postback（ボタン押下） =====
        if et == "postback":
            data = (ev.get("postback") or {}).get("data") or ""
            parts = data.split(":")
            kind = parts[0] if parts else ""
            ticker = (parts[1] if len(parts) > 1 else "").upper()
            if not ticker:
                continue

            # --- 発注メモ保存 ---
            if kind == "save":
                _save_action(user, ticker, "save_order", "from_line_button")

                # TrendResult からスナップショットを取得
                tr = (
                    TrendResult.objects.filter(user=user, ticker=ticker)
                    .order_by("-asof", "-updated_at")
                    .first()
                )
                name = _jpx_name(ticker, getattr(tr, "name", None))
                entry_price = getattr(tr, "entry_price_hint", None) or getattr(tr, "close_price", None)
                try:
                    entry_price = int(entry_price) if entry_price else None
                except Exception:
                    entry_price = None

                OrderMemo.objects.create(
                    user=user,
                    ticker=ticker,
                    name=name,
                    window="line",
                    entry_price=entry_price,
                    tp_price=None,
                    sl_price=None,
                    score=(getattr(tr, "overall_score", None) if tr else None),
                    weekly_trend=(getattr(tr, "weekly_trend", "") if tr else ""),
                    slope_yr=(getattr(tr, "slope_annual", None) if tr else None),
                    theme=(getattr(tr, "theme_score", None) if tr else None),
                    trend_snapshot=(tr.to_dict() if tr and hasattr(tr, "to_dict") else None),
                    meta={"via": "line_postback", "at": dj_now().isoformat()},
                    source="line",
                )

                disp = f"{name}（{ticker}）"
                _reply_line(reply_token, f"📝 発注メモに保存しました：{disp}")
                continue

            # --- 見送り ---
            elif kind == "reject":
                _save_action(user, ticker, "reject", "from_line_button")
                name = _jpx_name(ticker)
                _reply_line(reply_token, f"🚫 見送りを記録しました：{name}（{ticker}）")
                continue

            # --- スヌーズ ---
            elif kind == "snooze":
                mins = 120
                try:
                    if len(parts) > 2:
                        mins = int(parts[2])
                except Exception:
                    pass
                until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                _save_action(user, ticker, "notify", f"snooze_until={until.isoformat()}")
                _reply_line(reply_token, f"⏱ {mins}分後にリマインドします：{ticker}")
                continue

            # --- その他 ---
            else:
                _save_action(user, ticker, "unknown", data)
                _reply_line(reply_token, f"ℹ️ 未対応アクション: {data}")
                continue

        # ===== テキストコマンド =====
        if et == "message" and (ev.get("message") or {}).get("type") == "text":
            text = (ev["message"].get("text") or "").strip()
            low = text.lower()
            if low.startswith("/save"):
                parts = text.split()
                t = parts[-1] if len(parts) > 1 else ""
                if t:
                    _save_action(user, t, "save_order", "from_line_text")
                    _reply_line(reply_token, f"✅ 発注メモに保存：{t}")
            elif low.startswith("/reject"):
                parts = text.split()
                t = parts[-1] if len(parts) > 1 else ""
                if t:
                    _save_action(user, t, "reject", "from_line_text")
                    _reply_line(reply_token, f"🛑 見送り：{t}")
            elif low.startswith("/snooze"):
                parts = text.split()
                t = parts[1] if len(parts) > 1 else ""
                mins = int(parts[2]) if len(parts) > 2 else 120
                if t:
                    until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                    _save_action(user, t, "notify", f"snooze_until={until.isoformat()}")
                    _reply_line(reply_token, f"⏱ {mins}分後にリマインド：{t}")
            else:
                _reply_line(reply_token, "コマンド: /save 7203.T, /reject 7203.T, /snooze 7203.T 120")
            continue

    return _ok()