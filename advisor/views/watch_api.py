from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST

from advisor.models import WatchEntry


def _no_store(resp: JsonResponse) -> JsonResponse:
    """スマホSafari等のキャッシュを完全無効化"""
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp


# ===========================================================
# LIST: ACTIVE のみ返す / 検索 & カーソルページング / キャッシュ無効
# ===========================================================
@login_required
@require_GET
def watch_list(request):
    """
    GET /advisor/api/watch/list/?q=...&cursor=0&limit=20
    レスポンス: {ok, items: [...], next_cursor}
    """
    try:
        q = (request.GET.get("q") or "").strip()
        limit = int(request.GET.get("limit", 20))
        cursor = int(request.GET.get("cursor", 0) or 0)

        qs = WatchEntry.objects.filter(
            user=request.user,
            status=WatchEntry.STATUS_ACTIVE,  # ← ACTIVE のみ
        )
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

        qs = qs.order_by("-updated_at")[cursor : cursor + limit + 1]

        items: List[Dict[str, Any]] = []
        for w in qs[:limit]:
            items.append(
                {
                    "ticker": w.ticker,
                    "name": w.name,
                    "note": w.note,
                    "reason_summary": getattr(w, "reason_summary", ""),
                    "reason_details": getattr(w, "reason_details", []),
                    "in_position": w.in_position,
                    "theme_label": getattr(w, "theme_label", ""),
                    "theme_score": float(getattr(w, "theme_score", 0) or 0),
                    "ai_win_prob": float(getattr(w, "ai_win_prob", 0) or 0),
                    "target_tp": getattr(w, "target_tp", "") or "",
                    "target_sl": getattr(w, "target_sl", "") or "",
                }
            )

        next_cursor: Optional[int] = cursor + limit if len(qs) > limit else None

        resp = JsonResponse({"ok": True, "items": items, "next_cursor": next_cursor})
        return _no_store(resp)

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


# ===========================================================
# UPSERT: IN/OUT トグルやメモ保存（冪等）
# ===========================================================
@login_required
@require_POST
def watch_upsert(request):
    """
    POST /advisor/api/watch/upsert/
    body: {ticker, in_position?, note?}
    - 存在しなければ新規作成（statusは既存維持。新規はACTIVE）
    - 与えられたフィールドだけ更新（冪等）
    レスポンス: {ok, id, status: "active"|"archived"}
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        tkr = (payload.get("ticker") or "").strip()
        if not tkr:
            return HttpResponseBadRequest("ticker required")

        defaults = {
            "name": payload.get("name", ""),
            "note": payload.get("note", "") if payload.get("note") is not None else "",
        }

        # 既存取得（アーカイブ含め1件拾う）
        obj = (
            WatchEntry.objects.filter(user=request.user, ticker=tkr)
            .order_by("-updated_at")
            .first()
        )

        if obj is None:
            # 新規は ACTIVE で作成
            obj = WatchEntry.objects.create(
                user=request.user,
                ticker=tkr,
                status=WatchEntry.STATUS_ACTIVE,
                name=defaults["name"],
                note=defaults["note"],
            )
        else:
            # 更新（与えられた項目のみ）
            changed = False
            if "note" in payload and payload.get("note") is not None:
                obj.note = payload.get("note") or ""
                changed = True
            if "in_position" in payload and payload.get("in_position") is not None:
                obj.in_position = bool(payload.get("in_position"))
                changed = True
            if "name" in payload and payload.get("name") is not None:
                obj.name = payload.get("name") or obj.name
                changed = True

            if changed:
                obj.save()

        status = "archived" if obj.status == WatchEntry.STATUS_ARCHIVED else "active"
        return JsonResponse({"ok": True, "id": obj.id, "status": status})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


# ===========================================================
# ARCHIVE: 非表示（冪等：既に非表示でも常に ok=True）
# ===========================================================
@login_required
@require_POST
def watch_archive(request):
    """
    POST /advisor/api/watch/archive/
    body: {ticker}
    - ACTIVE → ARCHIVED に変更
    - 既に ARCHIVED / 存在しない → 常に ok=True + status="already_archived"
    レスポンス: {ok, status: "archived"|"already_archived", id?: number}
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
        tkr = (payload.get("ticker") or "").strip()
        if not tkr:
            return HttpResponseBadRequest("ticker required")

        qs = WatchEntry.objects.filter(user=request.user, ticker=tkr)
        if not qs.exists():
            # 存在しなくても冪等成功
            return JsonResponse({"ok": True, "status": "already_archived", "id": None})

        obj = qs.first()
        if obj.status != WatchEntry.STATUS_ARCHIVED:
            obj.status = WatchEntry.STATUS_ARCHIVED
            obj.save(update_fields=["status", "updated_at"])
            return JsonResponse({"ok": True, "status": "archived", "id": obj.id})

        # すでにアーカイブ済み
        return JsonResponse({"ok": True, "status": "already_archived", "id": None})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)