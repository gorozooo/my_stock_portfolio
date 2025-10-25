from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse, HttpResponseBadRequest
from django.utils.timezone import now
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from advisor.models import WatchEntry


# ---------- 共通ユーティリティ ----------
def _no_store(resp: JsonResponse) -> JsonResponse:
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp

def _ok(payload: dict) -> JsonResponse:
    return _no_store(JsonResponse(payload))

def _err(msg: str, status: int = 400) -> JsonResponse:
    return _no_store(JsonResponse({"ok": False, "error": msg}, status=status))

def _norm_ticker(s: str) -> str:
    return (s or "").strip().upper()


# ===================== LIST（IDを返す） =====================
@login_required
@require_GET
def watch_list(request):
    """
    GET /advisor/api/watch/list/?q=...&cursor=0&limit=20
    → {ok, items:[{id, ticker, name, ...}], next_cursor}
    """
    try:
        q = (request.GET.get("q") or "").strip()
        limit = int(request.GET.get("limit", 20))
        cursor = int(request.GET.get("cursor", 0) or 0)

        qs = WatchEntry.objects.filter(user=request.user, status=WatchEntry.STATUS_ACTIVE)
        if q:
            qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))
        qs = qs.order_by("-updated_at")[cursor : cursor + limit + 1]

        items: List[Dict[str, Any]] = []
        for w in qs[:limit]:
            items.append({
                "id": w.id,
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
                "added_at": (w.created_at.isoformat() if getattr(w, "created_at", None) else None),
            })

        next_cursor: Optional[int] = cursor + limit if len(qs) > limit else None
        print("[watch_list]", "user=", getattr(request.user, "id", None),
              "q=", q, "cursor=", cursor, "items=", len(items), "next=", next_cursor)
        return _ok({"ok": True, "items": items, "next_cursor": next_cursor})
    except Exception as e:
        print("[watch_list][ERROR]", repr(e))
        return _err(str(e))


# ===================== UPSERT（ID優先・旧ticker互換） =====================
@login_required
@csrf_exempt
@require_POST
@transaction.atomic
def watch_upsert(request):
    """
    POST /advisor/api/watch/upsert/
    body: {id?, ticker?, in_position?, note?, name?}
    - id があればその行だけ更新
    - id 無しは旧互換：tickerで upsert（大文字化・前後trim）
    - 変更があり、かつ ARCHIVED なら ACTIVE に復帰
    """
    try:
        p = json.loads(request.body.decode("utf-8") or "{}")
        rec_id = p.get("id")
        changed = False
        created = False

        if rec_id:
            obj = WatchEntry.objects.filter(id=rec_id, user=request.user).first()
            if not obj:
                return _err("record not found", 404)
        else:
            raw_tkr = (p.get("ticker") or "")
            norm_tkr = _norm_ticker(raw_tkr)
            if not norm_tkr:
                return _err("ticker or id required", 400)

            obj = (WatchEntry.objects
                   .filter(user=request.user, ticker__iexact=norm_tkr)
                   .order_by("-updated_at", "-id").first())
            if obj is None:
                obj = WatchEntry.objects.create(
                    user=request.user,
                    ticker=norm_tkr,
                    status=WatchEntry.STATUS_ACTIVE,
                    name=p.get("name", "") or "",
                    note=p.get("note", "") or "",
                )
                created = True
            else:
                if obj.ticker != norm_tkr:
                    obj.ticker = norm_tkr
                    changed = True

        if "name" in p and p.get("name") is not None:
            new = p.get("name") or ""
            if new != obj.name:
                obj.name = new; changed = True
        if "note" in p and p.get("note") is not None:
            new = p.get("note") or ""
            if new != obj.note:
                obj.note = new; changed = True
        if "in_position" in p and p.get("in_position") is not None:
            new = bool(p.get("in_position"))
            if new != obj.in_position:
                obj.in_position = new; changed = True

        if changed or created:
            if obj.status == WatchEntry.STATUS_ARCHIVED:
                obj.status = WatchEntry.STATUS_ACTIVE
            obj.updated_at = now()
            obj.save()

        status = "archived" if obj.status == WatchEntry.STATUS_ARCHIVED else "active"
        print("[watch_upsert]", "user=", getattr(request.user, "id", None),
              "id=", obj.id, "created=", created, "changed=", changed, "status=", status)
        return _ok({"ok": True, "id": obj.id, "status": status})

    except Exception as e:
        print("[watch_upsert][ERROR]", repr(e))
        return _err(str(e))


# ===================== ARCHIVE（ID推奨・重複ARCHIVED掃除） =====================
@login_required
@csrf_exempt
@require_POST
@transaction.atomic
def watch_archive(request):
    """
    POST /advisor/api/watch/archive/
    body: {id}                    ← ★基本はこれ（IDで一意）
      or {ticker}（旧互換）      ← __iexact で ACTIVE 全件アーカイブ

    ※ モデルに unique_together(user, ticker, status) があるため、
       更新前に同一(ticker, ARCHIVED)の残骸を掃除してから更新する。
    """
    try:
        p = json.loads(request.body.decode("utf-8") or "{}")
        rec_id = p.get("id")

        if rec_id:
            obj = WatchEntry.objects.filter(id=rec_id, user=request.user).first()
            if not obj:
                return _ok({"ok": True, "status": "already_archived", "id": None})

            # ★ 同一tickerのARCHIVEDを事前削除（ユニーク制約対策）
            WatchEntry.objects.filter(
                user=request.user, ticker=obj.ticker,
                status=WatchEntry.STATUS_ARCHIVED
            ).exclude(id=obj.id).delete()

            if obj.status != WatchEntry.STATUS_ARCHIVED:
                obj.status = WatchEntry.STATUS_ARCHIVED
                obj.updated_at = now()
                obj.save(update_fields=["status", "updated_at"])
                print("[watch_archive] by id → archived", "id=", obj.id)
                return _ok({"ok": True, "status": "archived", "id": obj.id})
            print("[watch_archive] by id → already", "id=", obj.id)
            return _ok({"ok": True, "status": "already_archived", "id": obj.id})

        # 旧クライアント互換：tickerでまとめてアーカイブ
        raw_tkr = (p.get("ticker") or "")
        norm_tkr = _norm_ticker(raw_tkr)
        if not norm_tkr:
            return _err("id or ticker required", 400)

        # 事前掃除：同じtickerのARCHIVEDを1つに集約（代表を残す or 全削除でも可）
        archiveds = list(WatchEntry.objects.filter(
            user=request.user, ticker__iexact=norm_tkr, status=WatchEntry.STATUS_ARCHIVED
        ).order_by("-updated_at", "-id"))
        for dup in archiveds[1:]:
            dup.delete()

        actives = WatchEntry.objects.filter(
            user=request.user, ticker__iexact=norm_tkr, status=WatchEntry.STATUS_ACTIVE
        )
        updated = actives.update(status=WatchEntry.STATUS_ARCHIVED, updated_at=now())

        if updated > 0:
            any_obj = (WatchEntry.objects
                       .filter(user=request.user, ticker__iexact=norm_tkr)
                       .order_by("-updated_at", "-id").first())
            print("[watch_archive] by ticker → archived", "tkr=", norm_tkr, "updated=", updated)
            return _ok({"ok": True, "status": "archived", "id": getattr(any_obj, "id", None)})

        print("[watch_archive] by ticker → already", "tkr=", norm_tkr)
        return _ok({"ok": True, "status": "already_archived", "id": None})

    except Exception as e:
        print("[watch_archive][ERROR]", repr(e))
        return _err(str(e))


# ====== デバッグ（必要なら残す/完成後は削除OK） ======
@login_required
@require_GET
def watch_ping(request):
    return _ok({"ok": True})

@login_required
@require_GET
def watch_archive_by_id_get(request, rec_id: int):
    """
    GET /advisor/api/watch/archive/id/<int:rec_id>/
    デバッグ用：idを確実にARCHIVEDへ（事前掃除込み）
    """
    obj = WatchEntry.objects.filter(id=rec_id, user=request.user).first()
    if not obj:
        return _err("not found", 404)

    WatchEntry.objects.filter(
        user=request.user, ticker=obj.ticker, status=WatchEntry.STATUS_ARCHIVED
    ).exclude(id=obj.id).delete()

    if obj.status != WatchEntry.STATUS_ARCHIVED:
        obj.status = WatchEntry.STATUS_ARCHIVED
        obj.updated_at = now()
        obj.save(update_fields=["status", "updated_at"])
        print("[watch_archive_by_id_get] archived id=", obj.id)
    else:
        print("[watch_archive_by_id_get] already id=", obj.id)

    return _ok({"ok": True, "id": obj.id, "status": "archived"})