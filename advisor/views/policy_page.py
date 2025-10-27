from __future__ import annotations
import json
from datetime import timedelta, timezone
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from advisor.models import Policy

JST = timezone(timedelta(hours=9))

def _no_store(resp: JsonResponse) -> JsonResponse:
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp

@login_required
def policy_page(request):
    return render(request, "advisor/policy.html", {})

# -------------------- 内部ヘルパ --------------------
def _labels(risk_mode: str, hold_style: str) -> dict:
    mode_map = {
        Policy.MODE_ATTACK:  "攻め",
        Policy.MODE_NORMAL:  "普通",
        Policy.MODE_DEFENSE: "守り",
        Policy.MODE_AUTO:    "おまかせ",
    }
    style_map = {
        Policy.STYLE_SHORT: "短期",
        Policy.STYLE_MID:   "中期",
        Policy.STYLE_LONG:  "長期",
        Policy.STYLE_AUTO:  "おまかせ",
    }
    return {"risk": mode_map.get(risk_mode, risk_mode), "style": style_map.get(hold_style, hold_style)}

def _resolve_auto(risk_mode: str, hold_style: str) -> dict:
    rm = risk_mode
    hs = hold_style
    if rm == Policy.MODE_AUTO:
        rm = Policy.MODE_NORMAL
    if hs == Policy.STYLE_AUTO:
        hs = Policy.STYLE_MID
    return {"risk_mode": rm, "hold_style": hs}

def _banner_text(rm: str, hs: str) -> str:
    # ✅ どちらか一方でも“おまかせ”のときにバナーを出す
    return "AIが今日の最適設定を自動選択中" if (
        rm == Policy.MODE_AUTO or hs == Policy.STYLE_AUTO
    ) else ""

# -------------------- API --------------------
@csrf_exempt
@login_required
def policy_api(request):
    user = request.user

    if request.method == "GET":
        pol, _ = Policy.objects.get_or_create(user=user)
        resolved = _resolve_auto(pol.risk_mode, pol.hold_style)
        banner = _banner_text(pol.risk_mode, pol.hold_style)
        data = {
            "ok": True,
            "current": {"risk_mode": pol.risk_mode, "hold_style": pol.hold_style,
                        "labels": _labels(pol.risk_mode, pol.hold_style)},
            "resolved": {**resolved, "labels": _labels(resolved["risk_mode"], resolved["hold_style"])},
            "banner": banner,
        }
        return _no_store(JsonResponse(data))

    if request.method == "POST":
        try:
            raw = request.body.decode("utf-8") if request.body else "{}"
            p = json.loads(raw or "{}")
            rm = (p.get("risk_mode") or "").strip() or Policy.MODE_NORMAL
            hs = (p.get("hold_style") or "").strip() or Policy.STYLE_MID

            valid_rm = {c[0] for c in Policy.MODE_CHOICES}
            valid_hs = {c[0] for c in Policy.STYLE_CHOICES}
            if rm not in valid_rm or hs not in valid_hs:
                return _no_store(JsonResponse({"ok": False, "error": "invalid_params"}, status=400))

            pol, _ = Policy.objects.get_or_create(user=user)
            pol.risk_mode = rm
            pol.hold_style = hs
            pol.save()

            resolved = _resolve_auto(rm, hs)
            banner = _banner_text(rm, hs)
            data = {
                "ok": True,
                "current": {"risk_mode": rm, "hold_style": hs,
                            "labels": _labels(rm, hs)},
                "resolved": {**resolved, "labels": _labels(resolved["risk_mode"], resolved["hold_style"])},
                "banner": banner,
            }
            return _no_store(JsonResponse(data))
        except Exception as e:
            return _no_store(JsonResponse({"ok": False, "error": str(e)}, status=400))

    return HttpResponseBadRequest("GET or POST only")