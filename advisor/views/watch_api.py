from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse, HttpResponseBadRequest
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt  # ★ 追加

from advisor.models import WatchEntry


def _no_store(resp: JsonResponse) -> JsonResponse:
    """スマホSafari等のキャッシュを完全無効化"""
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp


def _ok(payload: dict) -> JsonResponse:
    """no-store を常に付けて返すショートカット"""
    return _no_store(JsonResponse(payload))


def _err(msg: str, status: int = 400) -> JsonResponse:
    return _no_store(JsonResponse({"ok": False, "error": msg}, status=status))


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

        print("[watch_list] user=", getattr(request.user, "id", None),
              "q=", q, "cursor=", cursor, "len(items)=", len(items),
              "next=", next_cursor)

        return _ok({"ok": True, "items": items, "next_cursor": next_cursor})

    except Exception as e:
        print("[watch_list][ERROR]", repr(e))
        return _err(str(e))


# ===========================================================
# UPSERT: IN/OUT トグルやメモ保存（冪等・重複吸収）
# ===========================================================
@login_required
@csrf_exempt            # ★ CSRF免除（スマホでのPOST失敗を防ぐ）
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
            return _err("ticker required", 400)

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

        if changed and obj.status == WatchEntry.STATUS_ARCHIVED:
            obj.status = WatchEntry.STATUS_ACTIVE
            changed = True

        if changed or created:
            obj.updated_at = now()
            obj.save()

        status = "archived" if obj.status == WatchEntry.STATUS_ARCHIVED else "active"

        print("[watch_upsert] user=", getattr(request.user, "id", None),
              "ticker=", tkr, "created=", created, "changed=", changed,
              "status=", status)

        return _ok({"ok": True, "id": obj.id, "status": status})

    except Exception as e:
        print("[watch_upsert][ERROR]", repr(e))
        return _err(str(e))


# ===========================================================
# ARCHIVE: 非表示（冪等・重複 ACTIVE をすべてアーカイブ）
# ===========================================================
@login_required
@csrf_exempt            # ★ CSRF免除（スマホでのPOST失敗を防ぐ）
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
            return _err("ticker required", 400)

        actives = WatchEntry.objects.filter(
            user=request.user, ticker=tkr, status=WatchEntry.STATUS_ACTIVE
        )

        updated_count = actives.update(status=WatchEntry.STATUS_ARCHIVED, updated_at=now())

        if updated_count > 0:
            any_obj = (
                WatchEntry.objects.filter(user=request.user, ticker=tkr)
                .order_by("-updated_at", "-id")
                .first()
            )
            print("[watch_archive] user=", getattr(request.user, "id", None),
                  "ticker=", tkr, "updated_count=", updated_count, "→ archived")
            return _ok({"ok": True, "status": "archived", "id": getattr(any_obj, "id", None)})

        print("[watch_archive] user=", getattr(request.user, "id", None),
              "ticker=", tkr, "updated_count=0 → already_archived")
        return _ok({"ok": True, "status": "already_archived", "id": None})

    except Exception as e:
        print("[watch_archive][ERROR]", repr(e))
        return _err(str(e))