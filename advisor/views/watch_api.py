# advisor/views/watch_api.py
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now as dj_now

from advisor.models import WatchEntry

def _no_store(resp: JsonResponse) -> JsonResponse:
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp

def _get_user(request):
    return getattr(request, "user", None) if hasattr(request, "user") else None

def _parse_body(request) -> Dict[str, Any]:
    """JSON or form のどちらでも受け取る"""
    if request.body:
        try:
            return json.loads(request.body.decode("utf-8"))
        except Exception:
            pass
    # fallback: form-encoded
    return {k: v for k, v in request.POST.items()}

def _serialize_item(w: WatchEntry) -> Dict[str, Any]:
    """watch.js が使うキーを網羅して返す（存在しないものは None/空に）"""
    return {
        "id": w.id,
        "ticker": w.ticker,
        "name": w.name,
        "status": w.status,
        "note": w.note or "",
        "reason_summary": w.reason_summary or "",
        "reason_details": w.reason_details or [],
        "theme_label": w.theme_label or "",
        "theme_score": w.theme_score,
        "ai_win_prob": w.ai_win_prob,
        "target_tp": w.target_tp or "",
        "target_sl": w.target_sl or "",
        "overall_score": w.overall_score,
        "weekly_trend": w.weekly_trend or "",
        "entry_price_hint": w.entry_price_hint,
        "tp_price": w.tp_price,
        "sl_price": w.sl_price,
        "tp_pct": w.tp_pct,
        "sl_pct": w.sl_pct,
        "position_size_hint": w.position_size_hint,
        "updated_at": w.updated_at.isoformat(),
    }

# ---- ping ----
def watch_ping(request):
    return _no_store(JsonResponse({"ok": True, "now": dj_now().isoformat()}))

# ---- list（ページング付）----
def watch_list(request):
    user = _get_user(request)
    if not user or not user.is_authenticated:
        return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))

    q = (request.GET.get("q") or "").strip()
    try:
        cursor = int(request.GET.get("cursor") or 0)
    except Exception:
        cursor = 0
    try:
        limit = max(1, min(100, int(request.GET.get("limit") or 20)))
    except Exception:
        limit = 20

    qs = WatchEntry.objects.filter(user=user, status=WatchEntry.STATUS_ACTIVE).order_by("-updated_at", "-id")
    if q:
        qs = qs.filter(ticker__icontains=q) | qs.filter(name__icontains=q)

    total = qs.count()
    rows = list(qs[cursor:cursor+limit])
    items = [_serialize_item(w) for w in rows]

    next_cursor = cursor + limit if cursor + limit < total else None
    return _no_store(JsonResponse({"ok": True, "items": items, "next_cursor": next_cursor}))

# ---- upsert（メモ保存・名称更新）----
@csrf_exempt
def watch_upsert(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")

    user = _get_user(request)
    if not user or not user.is_authenticated:
        return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))

    try:
        p = _parse_body(request)
        ticker = (p.get("ticker") or "").strip().upper()
        if not ticker:
            return _no_store(JsonResponse({"ok": False, "error": "ticker_required"}, status=400))

        # 受け付けるキーだけ反映（その他は無視）
        defaults = {
            "name": p.get("name", "") or "",
            "note": p.get("note", "") or "",
        }
        rec, _created = WatchEntry.objects.update_or_create(
            user=user,
            ticker=ticker,
            status=WatchEntry.STATUS_ACTIVE,
            defaults=defaults,
        )
        return _no_store(JsonResponse({"ok": True, "id": rec.id}))
    except Exception as e:
        return _no_store(JsonResponse({"ok": False, "error": str(e)}, status=400))

# ---- archive（POST/GET どちらも許可）----
@csrf_exempt
def watch_archive(request):
    user = _get_user(request)
    if not user or not user.is_authenticated:
        return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))

    try:
        p = _parse_body(request) if request.method == "POST" else request.GET
        ticker = (p.get("ticker") or "").strip().upper()
        if not ticker:
            return _no_store(JsonResponse({"ok": False, "error": "ticker_required"}, status=400))

        rec = WatchEntry.objects.filter(user=user, ticker=ticker, status=WatchEntry.STATUS_ACTIVE).first()
        if not rec:
            return _no_store(JsonResponse({"ok": True, "status": "already_archived"}))

        rec.status = WatchEntry.STATUS_ARCHIVED
        rec.save(update_fields=["status", "updated_at"])
        return _no_store(JsonResponse({"ok": True, "status": "archived", "id": rec.id}))
    except Exception as e:
        return _no_store(JsonResponse({"ok": False, "error": str(e)}, status=400))

# ---- archive by id（GET）----
def watch_archive_by_id_get(request, rec_id: int):
    user = _get_user(request)
    if not user or not user.is_authenticated:
        return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))

    try:
        rec = WatchEntry.objects.filter(id=rec_id, user=user).first()
        if not rec:
            return _no_store(JsonResponse({"ok": False, "error": "not_found"}, status=404))
        if rec.status == WatchEntry.STATUS_ARCHIVED:
            return _no_store(JsonResponse({"ok": True, "status": "already_archived", "id": rec.id}))
        rec.status = WatchEntry.STATUS_ARCHIVED
        rec.save(update_fields=["status", "updated_at"])
        return _no_store(JsonResponse({"ok": True, "status": "archived", "id": rec.id}))
    except Exception as e:
        return _no_store(JsonResponse({"ok": False, "error": str(e)}, status=400))