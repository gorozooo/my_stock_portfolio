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
# UPSERT: IN/OUT トグルやメモ保存（冪等・重複吸収）
# ===========================================================
@login_required
@require_POST
@transaction.atomic
def watch_upsert(request):
    """
    POST /advisor/api/watch/upsert/
    body: {ticker, in_position?, note?, name?}

    方針:
    - 同一 (user, ticker) の既存レコードが複数あっても「最新1件」を採用し、他は放置（※後でクリーンアップ可）
    - 更新時、何らかの属性（note/in_position/name）が与えられたら「必要に応じて ACTIVE に復帰」
      → これで“再ウォッチ”操作が素直に反映される
    """
    try:
        p = json.loads(request.body.decode("utf-8") or "{}")
        tkr = (p.get("ticker") or "").strip()
        if not tkr:
            return HttpResponseBadRequest("ticker required")

        # 最新の1件を採用（存在しなければ新規）
        obj = (
            WatchEntry.objects.filter(user=request.user, ticker=tkr)
            .order_by("-updated_at", "-id")
            .first()
        )
        created = False
        if obj is None:
            obj = WatchEntry.objects.create(
                user=request.user,
                ticker=tkr,
                status=WatchEntry.STATUS_ACTIVE,
                name=p.get("name", "") or "",
                note=p.get("note", "") or "",
            )
            created = True

        # 値の反映
        changed = False
        if "name" in p and p.get("name") is not None:
            new_name = p.get("name") or ""
            if new_name != obj.name:
                obj.name = new_name
                changed = True
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

        # 何らかの更新があり、かつ ARCHIVED なら ACTIVE に復帰
        if changed and obj.status == WatchEntry.STATUS_ARCHIVED:
            obj.status = WatchEntry.STATUS_ACTIVE
            changed = True

        if changed or created:
            obj.updated_at = now()
            obj.save()

        status = "archived" if obj.status == WatchEntry.STATUS_ARCHIVED else "active"
        return JsonResponse({"ok": True, "id": obj.id, "status": status})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


# ===========================================================
# ARCHIVE: 非表示（冪等・重複 ACTIVE をすべてアーカイブ）
# ===========================================================
@login_required
@require_POST
@transaction.atomic
def watch_archive(request):
    """
    POST /advisor/api/watch/archive/
    body: {ticker}

    仕様:
    - 同一 (user, ticker) の ACTIVE レコードを **全て** ARCHIVED に更新（重複があっても確実に消える）
    - ACTIVE が 0 件なら "already_archived"
    """
    try:
        p = json.loads(request.body.decode("utf-8") or "{}")
        tkr = (p.get("ticker") or "").strip()
        if not tkr:
            return HttpResponseBadRequest("ticker required")

        actives = WatchEntry.objects.filter(
            user=request.user, ticker=tkr, status=WatchEntry.STATUS_ACTIVE
        )

        updated_count = actives.update(status=WatchEntry.STATUS_ARCHIVED, updated_at=now())

        if updated_count > 0:
            # 代表ID（見つかったものの1つ）を返すだけ
            any_obj = (
                WatchEntry.objects.filter(user=request.user, ticker=tkr)
                .order_by("-updated_at", "-id")
                .first()
            )
            return JsonResponse({"ok": True, "status": "archived", "id": getattr(any_obj, "id", None)})

        # ACTIVEが無ければ既にアーカイブ済み
        return JsonResponse({"ok": True, "status": "already_archived", "id": None})

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)