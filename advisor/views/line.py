# advisor/views/line.py
from __future__ import annotations
import json, os, hmac, hashlib, base64
from datetime import date, timedelta, timezone
from typing import Optional

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now as dj_now
from django.contrib.auth import get_user_model

import requests

from advisor.models import ActionLog
from advisor.models_trend import TrendResult
from advisor.models_order import OrderMemo

# 厳密TP/SL計算（任意）
try:
    from advisor.services.policy_rules import compute_exit_targets  # type: ignore
except Exception:
    compute_exit_targets = None  # type: ignore

JST = timezone(timedelta(hours=9))

# ====== JPX銘柄名（data/tse_list.json）======
def _tse_path() -> str:
    base = os.getcwd()
    try:
        from django.conf import settings
        base = getattr(settings, "BASE_DIR", base)
    except Exception:
        pass
    return os.path.join(base, "data", "tse_list.json")

def _load_tse_map() -> dict:
    p = _tse_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}

_TSE = _load_tse_map()

def _jpx_name(ticker: str, fallback: Optional[str] = None) -> str:
    t = (ticker or "").upper().strip()
    if t.endswith(".T"): t = t[:-2]
    v = _TSE.get(t) or {}
    nm = (v.get("name") or "").strip()
    return nm or (fallback or t)

# ====== LINE reply（SDKなしで直叩き）======
def _reply_line(reply_token: str, text: str) -> None:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
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

def _verify_signature(request: HttpRequest) -> bool:
    secret = os.getenv("LINE_CHANNEL_SECRET")
    if not secret:
        return True  # 開発用
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

# ====== 直近通知から window を推測 ======
def _guess_window_from_logs(user, ticker: str) -> str:
    rec = (
        ActionLog.objects.filter(user=user, ticker=ticker.upper(), action="notify")
        .order_by("-created_at")
        .first()
    )
    if rec and rec.note:
        # 例: "window=preopen; policies=..."
        try:
            seg = [s.strip() for s in str(rec.note).split(";") if s.strip()]
            for s in seg:
                if s.startswith("window="):
                    return s.split("=", 1)[1].strip()
        except Exception:
            pass
    return "line"

# ====== TrendResult から entry 推定 ======
def _latest_tr(user, ticker: str) -> Optional[TrendResult]:
    today = date.today()
    q = TrendResult.objects.filter(user=user, ticker=ticker.upper(), asof=today).order_by("-updated_at")
    tr = q.first()
    if tr:
        return tr
    return (
        TrendResult.objects.filter(user=user, ticker=ticker.upper())
        .order_by("-asof", "-updated_at")
        .first()
    )

def _int_or_none(v) -> Optional[int]:
    try:
        x = int(v)
        return x if x > 0 else None
    except Exception:
        return None

def _compute_exits(entry: Optional[int], ticker: str, tr: Optional[TrendResult]) -> tuple[Optional[int], Optional[int]]:
    if not entry:
        return (None, None)
    # policy_rules があれば優先
    if compute_exit_targets is not None:
        try:
            xt = compute_exit_targets(
                policy={"targets": {}, "exits": {}},
                ticker=ticker.upper(),
                entry_price=entry,
                days_held=None,
                atr14_hint=(getattr(tr, "notes", {}) or {}).get("atr14") if tr else None,
            )
            tp = _int_or_none(getattr(xt, "tp_price", None))
            sl = _int_or_none(getattr(xt, "sl_price", None))
            if tp or sl:
                return (tp, sl)
        except Exception:
            pass
    # フォールバック: TP +20%, SL -2.5%
    return (int(round(entry * 1.20)), int(round(entry * 0.975)))

# ====== Webhook 本体 ======
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

        # ===== Postback（ボタン押下）=====
        if et == "postback":
            data = (ev.get("postback") or {}).get("data") or ""
            parts = data.split(":")
            kind = parts[0] if parts else ""
            ticker = (parts[1] if len(parts) > 1 else "").upper()
            if not ticker:
                continue

            if kind == "save":
                # 1) 行動ログ
                _save_action(user, ticker, "save_order", "from_line_button")
                # 2) TrendResult から値を推定
                tr = _latest_tr(user, ticker)
                entry = _int_or_none(
                    getattr(tr, "entry_price_hint", None) or getattr(tr, "close_price", None)
                )
                tp, sl = _compute_exits(entry, ticker, tr)
                name = _jpx_name(ticker, getattr(tr, "name", None))
                window = _guess_window_from_logs(user, ticker)
                # 3) OrderMemo 保存
                OrderMemo.objects.create(
                    user=user,
                    ticker=ticker,
                    name=name,              # ←「テスト」固定を廃止
                    entry_price=entry,
                    tp_price=tp,
                    sl_price=sl,
                    window=window,
                    source="line",
                )
                # 4) 返信（JP名＋ティッカー）
                _reply_line(reply_token, f"📝 発注メモに保存しました：{name}（{ticker}）")
            elif kind == "reject":
                _save_action(user, ticker, "reject", "from_line_button")
                jp = _jpx_name(ticker, None)
                _reply_line(reply_token, f"🚫 見送りを記録しました：{jp}（{ticker}）")
            elif kind == "snooze":
                mins = 120
                try:
                    if len(parts) > 2:
                        mins = int(parts[2])
                except Exception:
                    pass
                until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                _save_action(user, ticker, "notify", f"snooze_until={until.isoformat()}")
                jp = _jpx_name(ticker, None)
                _reply_line(reply_token, f"⏱ {mins}分後に再通知：{jp}（{ticker}）")
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
                    tr = _latest_tr(user, t)
                    entry = _int_or_none(
                        getattr(tr, "entry_price_hint", None) or getattr(tr, "close_price", None)
                    )
                    tp, sl = _compute_exits(entry, t, tr)
                    name = _jpx_name(t, getattr(tr, "name", None))
                    window = _guess_window_from_logs(user, t)
                    OrderMemo.objects.create(
                        user=user, ticker=t.upper(), name=name,
                        entry_price=entry, tp_price=tp, sl_price=sl,
                        window=window, source="line",
                    )
                    _reply_line(reply_token, f"📝 発注メモに保存：{name}（{t.upper()}）")
            elif low.startswith("/reject"):
                parts = text.split()
                t = parts[-1] if len(parts) > 1 else ""
                if t:
                    _save_action(user, t, "reject", "from_line_text")
                    _reply_line(reply_token, f"🚫 見送り：{_jpx_name(t)}（{t.upper()}）")
            elif low.startswith("/snooze"):
                parts = text.split()
                t = parts[1] if len(parts) > 1 else ""
                mins = int(parts[2]) if len(parts) > 2 else 120
                if t:
                    until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                    _save_action(user, t, "notify", f"snooze_until={until.isoformat()}")
                    _reply_line(reply_token, f"⏱ {mins}分後にリマインド：{_jpx_name(t)}（{t.upper()}）")
            else:
                _reply_line(reply_token, "コマンド: /save 7203.T, /reject 7203.T, /snooze 7203.T 120")
            continue

    return _ok()