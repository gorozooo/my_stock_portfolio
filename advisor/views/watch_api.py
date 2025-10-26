from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse, HttpResponseBadRequest
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST

from advisor.models import WatchEntry


def _no_store(resp: JsonResponse) -> JsonResponse:
    """スマホSafari等のキャッシュを完全無効化"""
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp


# ===========================================================
# LIST: ACTIVE のみ返す / 検索 & カーソルページング / ボードと同じ項目を返す
# ===========================================================
@login_required
@require_GET
def watch_list(request):
    """
    GET /advisor/api/watch/list/?q=...&cursor=0&limit=20
    レスポンス: {ok, items:[...], next_cursor}
    """
    try:
        q = (request.GET.get("q") or "").strip()
        limit = int(request.GET.get("limit", 20))
        cursor = int(request.GET.get("cursor", 0) or 0)

        qs = WatchEntry.objects.filter(
            user=request.user,
            status=WatchEntry.STATUS_ACTIVE,
        )
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

        qs = qs.order_by("-updated_at")[cursor : cursor + limit + 1]

        items: List[Dict[str, Any]] = []
        for w in qs[:limit]:
            # ボードと合う形で項目を返す（存在しないものはNone/""）
            items.append(
                {
                    "id": w.id,
                    "ticker": w.ticker,
                    "name": w.name,
                    # 見出し周り
                    "weekly_trend": getattr(w, "weekly_trend", "") or "",    # "up"|"flat"|"down"|""
                    "overall_score": getattr(w, "overall_score", None),
                    # 理由
                    "reason_summary": getattr(w, "reason_summary", "") or "",
                    "reason_details": getattr(w, "reason_details", []) or [],
                    # ターゲット（％と価格の両方）
                    "target_tp": getattr(w, "target_tp", "") or "",
                    "target_sl": getattr(w, "target_sl", "") or "",
                    "entry_price_hint": getattr(w, "entry_price_hint", None),
                    "tp_price": getattr(w, "tp_price", None),
                    "sl_price": getattr(w, "sl_price", None),
                    "tp_pct": getattr(w, "tp_pct", None),
                    "sl_pct": getattr(w, "sl_pct", None),
                    # テーマ・AI
                    "theme_label": getattr(w, "theme_label", "") or "",
                    "theme_score": float(getattr(w, "theme_score", 0) or 0),
                    "ai_win_prob": float(getattr(w, "ai_win_prob", 0) or 0),
                    # 追加情報
                    "position_size_hint": getattr(w, "position_size_hint", None),
                    "in_position": w.in_position,
                    "updated_at": w.updated_at.isoformat(),
                    "created_at": w.created_at.isoformat(),
                }
            )

        next_cursor: Optional[int] = cursor + limit if len(qs) > limit else None
        return _no_store(JsonResponse({"ok": True, "items": items, "next_cursor": next_cursor}))

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


# ===========================================================
# UPSERT: メモ保存やIN/OUTトグル（与えた値のみ変更）
# ===========================================================
@login_required
@require_POST
@transaction.atomic
def watch_upsert(request):
    """
    POST /advisor/api/watch/upsert/
    body: {id?, ticker, note?, in_position?}
    """
    try:
        p = json.loads(request.body.decode("utf-8") or "{}")
        tkr = (p.get("ticker") or "").strip()
        if not tkr:
            return HttpResponseBadRequest("ticker required")

        obj = (
            WatchEntry.objects.filter(user=request.user, ticker=tkr)
            .order_by("-updated_at", "-id")
            .first()
        )
        created = False
        if obj is None:
            obj = WatchEntry.objects.create(user=request.user, ticker=tkr, status=WatchEntry.STATUS_ACTIVE)
            created = True

        changed = False
        if "note" in p and p.get("note") is not None:
            new_note = p.get("note") or ""
            if new_note != obj.note:
                obj.note = new_note
                changed = True
        if "in_position" in p and p.get("in_position") is not None:
            new_in = bool(p.get("in_position"))
            if new_in != obj.in_position:
                obj.in_position = new_in
                changed = True

        if changed or created:
            obj.updated_at = now()
            obj.save(update_fields=["note", "in_position", "updated_at"])

        return JsonResponse({"ok": True, "id": obj.id})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


# ===========================================================
# ARCHIVE: 非表示（冪等）
# ===========================================================
@login_required
@require_POST
@transaction.atomic
def watch_archive(request):
    """
    POST /advisor/api/watch/archive/
    body: {ticker}
    """
    try:
        p = json.loads(request.body.decode("utf-8") or "{}")
        tkr = (p.get("ticker") or "").strip()
        if not tkr:
            return HttpResponseBadRequest("ticker required")

        actives = WatchEntry.objects.filter(user=request.user, ticker=tkr, status=WatchEntry.STATUS_ACTIVE)
        updated_count = actives.update(status=WatchEntry.STATUS_ARCHIVED, updated_at=now())

        status = "archived" if updated_count > 0 else "already_archived"
        return JsonResponse({"ok": True, "status": status})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


# ---- 追加：IDでGETアーカイブ（既に動作確認済みのルート）----
@login_required
@require_GET
@transaction.atomic
def watch_archive_by_id_get(request, rec_id: int):
    obj = WatchEntry.objects.filter(user=request.user, id=rec_id).first()
    if not obj:
        return _no_store(JsonResponse({"ok": False, "error": "not_found"}, status=404))
    if obj.status != WatchEntry.STATUS_ARCHIVED:
        obj.status = WatchEntry.STATUS_ARCHIVED
        obj.save(update_fields=["status", "updated_at"])
    return _no_store(JsonResponse({"ok": True, "id": obj.id, "status": "archived"}))


def watch_ping(request):
    return _no_store(JsonResponse({"ok": True}))