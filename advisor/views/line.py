# advisor/views/line.py
from __future__ import annotations
import json, os, hmac, hashlib, base64
from datetime import timedelta, timezone
from typing import Optional, Tuple

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now as dj_now
from django.contrib.auth import get_user_model

import requests
from advisor.models import ActionLog
from advisor.models_order import OrderMemo
from advisor.models_trend import TrendResult

# 可能なら notify 側の日本語マップを再利用（無ければフォールバック）
try:
    from advisor.services.notify import _jpx_name  # type: ignore
except Exception:
    _jpx_name = None  # type: ignore

# 可能なら厳密ターゲット計算を利用（無ければTP/SLフォールバック）
try:
    from advisor.services.policy_rules import compute_exit_targets  # type: ignore
except Exception:
    compute_exit_targets = None  # type: ignore

JST = timezone(timedelta(hours=9))

# ====== 返信ヘルパ（SDKなしで /reply 直叩き） ======
def _reply_line(reply_token: str, text: str) -> None:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
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
    # 開発用バイパス（本番では付けないこと）
    if request.GET.get("bypass") == "1":
        return True
    secret = os.getenv("LINE_CHANNEL_SECRET")
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

# ---------- 価格・銘柄取得ヘルパ ----------
def _latest_tr(user, ticker: str) -> Optional[TrendResult]:
    return (
        TrendResult.objects.filter(user=user, ticker=ticker.upper())
        .order_by("-asof", "-updated_at")
        .first()
    )

def _jp_name(ticker: str, fallback: Optional[str]) -> str:
    if _jpx_name:
        try:
            return _jpx_name(ticker, fallback)
        except Exception:
            pass
    # フォールバック：英名→そのまま / コードだけは .T を付与
    t = (ticker or "").upper().strip()
    if t.endswith(".T"):
        code = t
    else:
        code = f"{t}.T" if t.isdigit() else t
    return (fallback or "").strip() or code

def _int_or_none(x) -> Optional[int]:
    try:
        v = int(round(float(x)))
        return v if v > 0 else None
    except Exception:
        return None

def _compute_exits(entry: Optional[int], ticker: str, tr: Optional[TrendResult]) -> Tuple[Optional[int], Optional[int]]:
    if not entry:
        return (None, None)
    # 厳密ロジックが使えるならそちらを優先
    if compute_exit_targets:
        try:
            xt = compute_exit_targets(
                policy={"targets":{}, "exits":{}},
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
    # フォールバック：TP +6% / SL -2%
    return (_int_or_none(entry * 1.06), _int_or_none(entry * 0.98))

def _save_order_memo(user, ticker: str, *, window: str = "preopen") -> Tuple[bool, str]:
    """
    OrderMemo を保存。成功 True, 表示名 を返す。
    """
    t = ticker.upper().strip()
    tr = _latest_tr(user, t)
    # 名称
    base_name = getattr(tr, "name", None)
    disp_name = _jp_name(t, base_name)
    show = f"{disp_name} ({t})"

    # 価格
    entry = _int_or_none(getattr(tr, "entry_price_hint", None) or getattr(tr, "close_price", None))
    tp, sl = _compute_exits(entry, t, tr)
    try:
        OrderMemo.objects.create(
            user=user,
            ticker=t,
            name=disp_name,      # admin の NAME に日本語名
            window=window,
            entry_price=entry,
            tp_price=tp,
            sl_price=sl,
            source="line",
        )
        return True, show
    except Exception as e:
        print("[OrderMemo save error]", e)
        # 最低限、価格なしでも名前だけで作っておくオプション
        try:
            OrderMemo.objects.create(
                user=user, ticker=t, name=disp_name, window=window, source="line"
            )
            return True, show
        except Exception as e2:
            print("[OrderMemo save fallback error]", e2)
            return False, show

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

            if kind == "save":
                ok, show = _save_order_memo(user, ticker, window="preopen")
                _save_action(user, ticker, "save_order", "from_line_button")
                if ok:
                    _reply_line(reply_token, f"📝 発注メモに保存しました：{show}")
                else:
                    _reply_line(reply_token, f"⚠️ 保存に失敗しました：{show}")
            elif kind == "reject":
                _save_action(user, ticker, "reject", "from_line_button")
                nm = _jp_name(ticker, None)
                _reply_line(reply_token, f"🚫 見送りを記録しました：{nm} ({ticker})")
            elif kind == "snooze":
                mins = 120
                try:
                    if len(parts) > 2:
                        mins = int(parts[2])
                except Exception:
                    pass
                until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                _save_action(user, ticker, "notify", f"snooze_until={until.isoformat()}")
                nm = _jp_name(ticker, None)
                _reply_line(reply_token, f"⏱ {mins}分後に再通知します：{nm} ({ticker})")
            else:
                _save_action(user, ticker, "unknown", data)
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
                    ok, show = _save_order_memo(user, t, window="preopen")
                    _save_action(user, t, "save_order", "from_line_text")
                    _reply_line(reply_token, "📝 発注メモに保存しました：" + show if ok else "⚠️ 保存に失敗しました：" + show)
            elif low.startswith("/reject"):
                parts = text.split()
                t = parts[-1] if len(parts) > 1 else ""
                if t:
                    _save_action(user, t, "reject", "from_line_text")
                    nm = _jp_name(t, None)
                    _reply_line(reply_token, f"🚫 見送りを記録しました：{nm} ({t})")
            elif low.startswith("/snooze"):
                parts = text.split()
                t = parts[1] if len(parts) > 1 else ""
                mins = int(parts[2]) if len(parts) > 2 else 120
                if t:
                    until = dj_now().astimezone(JST) + timedelta(minutes=mins)
                    _save_action(user, t, "notify", f"snooze_until={until.isoformat()}")
                    nm = _jp_name(t, None)
                    _reply_line(reply_token, f"⏱ {mins}分後に再通知します：{nm} ({t})")
            else:
                _reply_line(reply_token, "コマンド: /save 7203.T, /reject 7203.T, /snooze 7203.T 120")
            continue

    return _ok()