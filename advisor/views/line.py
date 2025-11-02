# advisor/views/line.py
from __future__ import annotations
import json, os, hmac, hashlib, base64
from datetime import timedelta, timezone, date
from typing import Optional, Tuple, Dict, Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now as dj_now
from django.contrib.auth import get_user_model

import requests

from advisor.models import ActionLog
from advisor.models_order import OrderMemo
from advisor.models_trend import TrendResult
from django.conf import settings

JST = timezone(timedelta(hours=9))

# ====== 返信ヘルパ（SDKなしで /reply 直叩き） ======
def _reply_line(reply_token: str, text: str) -> None:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token or not reply_token:
        return
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {"Authorization": f"Bearer {token}", "Content-Type":"application/json"}
    body = {"replyToken": reply_token, "messages":[{"type":"text","text": text}]}
    try:
        r = requests.post(url, headers=headers, data=json.dumps(body))
        print("[LINE reply]", r.status_code, r.text[:200])
    except Exception as e:
        print("[LINE reply error]", e)

def _verify_signature(request: HttpRequest) -> bool:
    # 開発用バイパス（curl 等の手動確認用）
    if request.GET.get("bypass") == "1":
        return True
    secret = os.getenv("LINE_CHANNEL_SECRET") or getattr(settings, "LINE_CHANNEL_SECRET", "")
    if not secret:
        return True  # 環境に無ければ検証スキップ（開発用）
    sig = request.headers.get("X-Line-Signature", "")
    raw = request.body
    mac = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    calc = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(calc, sig)

def _actor():
    U = get_user_model()
    return U.objects.first()

def _ok():
    return JsonResponse({"ok": True})

def _save_action(user, ticker: str, action: str, note: str = ""):
    ActionLog.objects.create(user=user, ticker=ticker.upper(), action=action, note=note)

# ===== JPX銘柄マップ（data/tse_list.json） =====
def _load_tse_map() -> Dict[str, Any]:
    base = getattr(settings, "BASE_DIR", os.getcwd())
    path = os.path.join(base, "data", "tse_list.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}

_TSE = _load_tse_map()

def _display_ticker(t: str) -> str:
    t = (t or "").upper().strip()
    if t.isdigit() and 4 <= len(t) <= 5:
        return f"{t}.T"
    return t

def _jpx_name(ticker: str, fallback: Optional[str]=None) -> str:
    t = (ticker or "").upper().strip()
    if t.endswith(".T"):
        t = t[:-2]
    rec = _TSE.get(t) or {}
    nm = (rec.get("name") or "").strip() if isinstance(rec, dict) else ""
    return nm or (fallback or _display_ticker(ticker))

def _latest_tr_today(user, ticker: str) -> Optional[TrendResult]:
    return (
        TrendResult.objects
        .filter(user=user, ticker=ticker.upper(), asof=date.today())
        .order_by("-updated_at")
        .first()
    )

def _guess_entry_price(tr: Optional[TrendResult]) -> Optional[int]:
    if not tr:
        return None
    try:
        return int(tr.entry_price_hint or tr.close_price or 0) or None
    except Exception:
        return None

def _save_order_memo(user, ticker: str) -> OrderMemo:
    """TrendResult と JPXマップから和名/価格を補完して OrderMemo を作成"""
    t_norm = _display_ticker(ticker)
    tr = _latest_tr_today(user, t_norm)
    # 名称は JPX最優先 → TrendResult.name → ティッカー
    name = _jpx_name(t_norm, getattr(tr, "name", None))
    entry_price = _guess_entry_price(tr)

    memo = OrderMemo.objects.create(
        user=user,
        ticker=t_norm,
        name=name,
        window="line",
        entry_price=entry_price,
        note="from_line_button",
    )
    return memo

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
            disp = _display_ticker(ticker)
            jpname = _jpx_name(disp, None)

            if kind == "save":
                # 1) ActionLog
                _save_action(user, disp, "save_order", "from_line_button")
                # 2) OrderMemo（★今回追加）
                try:
                    memo = _save_order_memo(user, disp)
                except Exception as e:
                    # 失敗しても返信は返す（原因はログに）
                    print("[OrderMemo save error]", e)
                    memo = None
                # 3) 返信（和名(コード) で返す）
                _reply_line(reply_token, f"📝 発注メモに保存しました：{jpname}({_display_ticker(ticker)})")

            elif kind == "reject":
                _save_action(user, disp, "reject", "from_line_button")
                _reply_line(reply_token, f"🚫 見送りを記録しました：{jpname}({_display_ticker(ticker)})")

            elif kind == "snooze":
                mins = 120
                try:
                    if len(parts) > 2:
                        mins = int(parts[2])
                except Exception:
                    pass
                until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                _save_action(user, disp, "notify", f"snooze_until={until.isoformat()}")
                _reply_line(reply_token, f"⏱ {mins}分後にリマインドします：{jpname}({_display_ticker(ticker)})")

            else:
                _save_action(user, disp, "unknown", data)
                _reply_line(reply_token, f"ℹ️ 未対応アクション: {data}")
            continue

        # ===== 任意：テキストコマンド（/save 等） =====
        if et == "message" and (ev.get("message") or {}).get("type") == "text":
            text = (ev["message"].get("text") or "").strip()
            low = text.lower()
            if low.startswith("/save"):
                parts = text.split()
                t = parts[-1] if len(parts) > 1 else ""
                if t:
                    disp = _display_ticker(t)
                    jpname = _jpx_name(disp, None)
                    _save_action(user, disp, "save_order", "from_line_text")
                    try:
                        _save_order_memo(user, disp)
                    except Exception as e:
                        print("[OrderMemo save error]", e)
                    _reply_line(reply_token, f"📝 発注メモに保存：{jpname}({disp})")

            elif low.startswith("/reject"):
                parts = text.split()
                t = parts[-1] if len(parts) > 1 else ""
                if t:
                    disp = _display_ticker(t)
                    jpname = _jpx_name(disp, None)
                    _save_action(user, disp, "reject", "from_line_text")
                    _reply_line(reply_token, f"🚫 見送り：{jpname}({disp})")

            elif low.startswith("/snooze"):
                parts = text.split()
                t = parts[1] if len(parts) > 1 else ""
                mins = int(parts[2]) if len(parts) > 2 else 120
                if t:
                    disp = _display_ticker(t)
                    jpname = _jpx_name(disp, None)
                    until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                    _save_action(user, disp, "notify", f"snooze_until={until.isoformat()}")
                    _reply_line(reply_token, f"⏱ {mins}分後にリマインド：{jpname}({disp})")

            else:
                _reply_line(reply_token, "コマンド: /save 7203.T, /reject 7203.T, /snooze 7203.T 120")
            continue

    return _ok()