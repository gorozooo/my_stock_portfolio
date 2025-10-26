# advisor/views/watch_api.py
from __future__ import annotations

import json
from typing import Dict, Any
from datetime import timedelta, timezone

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from django.utils.timezone import now as dj_now

from advisor.models import WatchEntry

JST = timezone(timedelta(hours=9))

def _log(*args):
    print("[advisor.watch_api]", *args)

def _no_store(resp: JsonResponse) -> JsonResponse:
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp

def _ensure_auth(request) -> bool:
    return hasattr(request, "user") and request.user and request.user.is_authenticated

def _parse_payload(request) -> Dict[str, Any]:
    """
    JSON / form / query の順で安全に取り出す。
    （端末差・ブラウザ差で body が空でも値を拾えるように）
    """
    # JSON
    try:
        if request.body:
            raw = request.body.decode("utf-8")
            if raw:
                return json.loads(raw)
    except Exception:
        pass
    # form
    if request.POST:
        return {k: request.POST.get(k) for k in request.POST.keys()}
    # query
    if request.GET:
        return {k: request.GET.get(k) for k in request.GET.keys()}
    return {}

# ---------- ping ----------
def watch_ping(request):
    return _no_store(JsonResponse({"ok": True, "now": dj_now().astimezone(JST).isoformat()}))

# ---------- serializer ----------
def _serialize_item(w: WatchEntry) -> Dict[str, Any]:
    reasons = w.reason_details if isinstance(w.reason_details, list) else [
        s.strip() for s in (w.reason_summary or "").split("/") if s.strip()
    ]
    return {
        "id": w.id,
        "ticker": w.ticker,
        "name": w.name,
        "note": w.note or "",
        "reason_summary": w.reason_summary or "",
        "reason_details": reasons,
        "theme_label": w.theme_label or "",
        "theme_score": w.theme_score,
        "ai_win_prob": w.ai_win_prob,
        "overall_score": w.overall_score or 0,
        "weekly_trend": w.weekly_trend or "",
        "entry_price_hint": w.entry_price_hint,
        "tp_price": w.tp_price,
        "sl_price": w.sl_price,
        "tp_pct": w.tp_pct,
        "sl_pct": w.sl_pct,
        "updated_at": w.updated_at.astimezone(JST).isoformat(timespec="minutes"),
        "status": w.status,
        # 将来用の確率（保存していなければ None）
        "ai_tp_prob": None,
        "ai_sl_prob": None,
    }

# ---------- list ----------
def watch_list(request):
    if not _ensure_auth(request):
        return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))

    user = request.user
    q = (request.GET.get("q") or "").strip()
    try:
        cursor = int(request.GET.get("cursor") or "0")
    except Exception:
        cursor = 0
    try:
        limit = max(1, min(50, int(request.GET.get("limit") or "20")))
    except Exception:
        limit = 20

    qs = WatchEntry.objects.filter(user=user, status=WatchEntry.STATUS_ACTIVE)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q) | Q(reason_summary__icontains=q))
    qs = qs.order_by("-updated_at", "-id")

    total = qs.count()
    rows = list(qs[cursor: cursor + limit])

    items = [_serialize_item(w) for w in rows]
    next_cursor = cursor + limit if (cursor + limit) < total else None

    return _no_store(JsonResponse({"ok": True, "items": items, "next_cursor": next_cursor}))

# ---------- upsert (note only; 冪等・入力取りこぼし防止) ----------
@csrf_exempt
def watch_upsert(request):
    if request.method not in ("POST", "GET"):  # GET を許可（fallback）
        return HttpResponseBadRequest("POST only")
    if not _ensure_auth(request):
        return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))

    try:
        p = _parse_payload(request)
        ticker = (p.get("ticker") or "").strip().upper()
        name = (p.get("name") or "").strip()
        note = p.get("note") or ""
        if not ticker:
            return _no_store(JsonResponse({"ok": False, "error": "ticker_required"}, status=400))

        # 直近のレコード（アクティブ優先、なければ最新）を拾う
        w = (
            WatchEntry.objects.filter(user=request.user, ticker=ticker, status=WatchEntry.STATUS_ACTIVE)
            .order_by("-updated_at", "-id")
            .first()
        ) or (
            WatchEntry.objects.filter(user=request.user, ticker=ticker)
            .order_by("-updated_at", "-id")
            .first()
        )

        if not w:
            w = WatchEntry(user=request.user, ticker=ticker, name=name or "")

        if name and not w.name:
            w.name = name
        w.note = note
        w.status = WatchEntry.STATUS_ACTIVE
        w.save()

        _log("watch_upsert ok id=", w.id)
        return _no_store(JsonResponse({"ok": True, "id": w.id, "status": w.status}))
    except Exception as e:
        _log("watch_upsert ERROR:", repr(e))
        return _no_store(JsonResponse({"ok": False, "error": str(e)}, status=400))

# ---------- archive (互換: POST /api/watch/archive/) ----------
@csrf_exempt
def watch_archive(request):
    """POST body で id または ticker を受け取りアーカイブ（既存仕様と互換）。"""
    if request.method != "POST":
        return HttpResponseBadRequest("POST only")
    if not _ensure_auth(request):
        return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))

    try:
        p = _parse_payload(request)
        rec_id = p.get("id")
        ticker = (p.get("ticker") or "").strip().upper()

        if rec_id:
            w = WatchEntry.objects.get(id=int(rec_id), user=request.user)
        elif ticker:
            w = (
                WatchEntry.objects.filter(user=request.user, ticker=ticker, status=WatchEntry.STATUS_ACTIVE)
                .order_by("-updated_at", "-id")
                .first()
            )
            if not w:
                return _no_store(JsonResponse({"ok": False, "error": "not_found"}, status=404))
        else:
            return _no_store(JsonResponse({"ok": False, "error": "id_or_ticker_required"}, status=400))

        if w.status == WatchEntry.STATUS_ARCHIVED:
            return _no_store(JsonResponse({"ok": True, "id": w.id, "status": "already_archived"}))

        w.status = WatchEntry.STATUS_ARCHIVED
        w.save(update_fields=["status", "updated_at"])
        _log("watch_archive archived id=", w.id)
        return _no_store(JsonResponse({"ok": True, "id": w.id, "status": "archived"}))
    except WatchEntry.DoesNotExist:
        return _no_store(JsonResponse({"ok": False, "error": "not_found"}, status=404))
    except Exception as e:
        _log("watch_archive ERROR:", repr(e))
        return _no_store(JsonResponse({"ok": False, "error": str(e)}, status=400))

# ---------- archive by id (GET; 冪等) ----------
def watch_archive_by_id_get(request, rec_id: int):
    if not _ensure_auth(request):
        return _no_store(JsonResponse({"ok": False, "error": "auth_required"}, status=401))
    try:
        w = WatchEntry.objects.get(id=rec_id, user=request.user)
        if w.status == WatchEntry.STATUS_ARCHIVED:
            return _no_store(JsonResponse({"ok": True, "id": w.id, "status": "already_archived"}))
        w.status = WatchEntry.STATUS_ARCHIVED
        w.save(update_fields=["status", "updated_at"])
        _log("watch_archive_by_id archived id=", w.id)
        return _no_store(JsonResponse({"ok": True, "id": w.id, "status": "archived"}))
    except WatchEntry.DoesNotExist:
        return _no_store(JsonResponse({"ok": False, "error": "not_found"}, status=404))
    except Exception as e:
        _log("watch_archive_by_id ERROR:", repr(e))
        return _no_store(JsonResponse({"ok": False, "error": str(e)}, status=400))