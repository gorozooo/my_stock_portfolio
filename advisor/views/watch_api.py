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
        qs = qs.order_by("-updated_at")[cursor:cursor + limit + 1]

        items: List[Dict[str, Any]] = []
        for w in qs[:limit]:
            items.append({
                "id": w.id,                         # ★ IDを返す（これを使う）
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
            })

        next_cursor: Optional[int] = cursor + limit if len(qs) > limit else None
        print("[watch_list]", "user=", getattr(request.user, "id", None),
              "q=", q, "cursor=", cursor, "items=", len(items), "next=", next_cursor)
        return _ok({"ok": True, "items": items, "next_cursor": next_cursor})
    except Exception as e:
        print("[watch_list][ERROR]", repr(e))
        return _err(str(e))


# ===================== UPSERT（ID優先） =====================
@login_required
@csrf_exempt
@require_POST
@transaction.atomic
def watch_upsert(request):
    """
    POST /advisor/api/watch/upsert/
    body: {id?, ticker?, in_position?, note?, name?}
    優先順位: id 指定があればそのレコードだけ更新。
             id 無しなら ticker で upsert（正規化）※後方互換。
    """
    try:
        p = json.loads(request.body.decode("utf-8") or "{}")
        rec_id = p.get("id")
        changed = False
        created = False

        if rec_id:
            # IDで一意に更新
            obj = WatchEntry.objects.filter(id=rec_id, user=request.user).first()
            if not obj:
                return _err("record not found", 404)
        else:
            # 旧クライアント互換：tickerでupsert
            raw_tkr = (p.get("ticker") or "")
            norm_tkr = _norm_ticker(raw_tkr)
            if not norm_tkr:
                return _err("ticker or id required", 400)
            obj = (WatchEntry.objects
                   .filter(user=request.user, ticker__iexact=norm_tkr)
                   .order_by("-updated_at", "-id").first())
            if obj is None:
                obj = WatchEntry.objects.create(
                    user=request.user, ticker=norm_tkr, status=WatchEntry.STATUS_ACTIVE,
                    name=p.get("name", "") or "", note=p.get("note", "") or ""
                )
                created = True
            else:
                # 保存時は正規化へ寄せる
                if obj.ticker != norm_tkr:
                    obj.ticker = norm_tkr
                    changed = True

        if "name" in p and p.get("name") is not None:
            new = p.get("name") or ""
            if new != obj.name: obj.name = new; changed = True
        if "note" in p and p.get("note") is not None:
            new = p.get("note") or ""
            if new != obj.note: obj.note = new; changed = True
        if "in_position" in p and p.get("in_position") is not None:
            new = bool(p.get("in_position"))
            if new != obj.in_position: obj.in_position = new; changed = True

        if changed or created:
            if obj.status == WatchEntry.STATUS_ARCHIVED:
                obj.status = WatchEntry.STATUS_ACTIVE      # 編集＝復帰
            obj.updated_at = now()
            obj.save()

        status = "archived" if obj.status == WatchEntry.STATUS_ARCHIVED else "active"
        print("[watch_upsert]", "user=", getattr(request.user, "id", None),
              "id=", obj.id, "created=", created, "changed=", changed, "status=", status)
        return _ok({"ok": True, "id": obj.id, "status": status})

    except Exception as e:
        print("[watch_upsert][ERROR]", repr(e))
        return _err(str(e))


# ===================== ARCHIVE（IDで非表示） =====================
@login_required
@csrf_exempt
@require_POST
@transaction.atomic
def watch_archive(request):
    """
    POST /advisor/api/watch/archive/
    body: {id}                   ← ★ 基本はこれ
      or {ticker}（旧互換）     ← 大小無視でACTIVE全件アーカイブ
    """
    try:
        p = json.loads(request.body.decode("utf-8") or "{}")
        rec_id = p.get("id")

        if rec_id:
            # IDでピンポイントにアーカイブ
            obj = WatchEntry.objects.filter(id=rec_id, user=request.user).first()
            if not obj:
                return _ok({"ok": True, "status": "already_archived", "id": None})
            if obj.status != WatchEntry.STATUS_ARCHIVED:
                obj.status = WatchEntry.STATUS_ARCHIVED
                obj.updated_at = now()
                obj.save(update_fields=["status", "updated_at"])
                print("[watch_archive] by id", "user=", getattr(request.user, "id", None),
                      "id=", obj.id, "→ archived")
                return _ok({"ok": True, "status": "archived", "id": obj.id})
            print("[watch_archive] by id already", "user=", getattr(request.user, "id", None),
                  "id=", obj.id)
            return _ok({"ok": True, "status": "already_archived", "id": obj.id})

        # 旧クライアント：tickerで処理（大小無視・ACTIVE全件）
        raw_tkr = (p.get("ticker") or "")
        norm_tkr = _norm_ticker(raw_tkr)
        if not norm_tkr:
            return _err("id or ticker required", 400)

        actives = WatchEntry.objects.filter(
            user=request.user, ticker__iexact=norm_tkr, status=WatchEntry.STATUS_ACTIVE
        )
        updated = actives.update(status=WatchEntry.STATUS_ARCHIVED, updated_at=now())
        if updated > 0:
            any_obj = (WatchEntry.objects
                       .filter(user=request.user, ticker__iexact=norm_tkr)
                       .order_by("-updated_at", "-id").first())
            print("[watch_archive] by ticker", "user=", getattr(request.user, "id", None),
                  "tkr=", norm_tkr, "updated=", updated)
            return _ok({"ok": True, "status": "archived", "id": getattr(any_obj, "id", None)})
        print("[watch_archive] by ticker already", "user=", getattr(request.user, "id", None),
              "tkr=", norm_tkr)
        return _ok({"ok": True, "status": "already_archived", "id": None})

    except Exception as e:
        print("[watch_archive][ERROR]", repr(e))
        return _err(str(e))


@login_required
@require_GET
def watch_archive_by_id_get(request, rec_id: int):
    """
    デバッグ用：GETで確実に id をアーカイブ（CSRF不要）
    /advisor/api/watch/archive/id/<int:rec_id>/
    """
    obj = WatchEntry.objects.filter(id=rec_id, user=request.user).first()
    if not obj:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)
    if obj.status != WatchEntry.STATUS_ARCHIVED:
        obj.status = WatchEntry.STATUS_ARCHIVED
        obj.updated_at = now()
        obj.save(update_fields=["status", "updated_at"])
        print("[watch_archive_by_id_get] archived id=", obj.id)
    else:
        print("[watch_archive_by_id_get] already archived id=", obj.id)
    return JsonResponse({"ok": True, "id": obj.id, "status": "archived"})

@login_required
@require_GET
def watch_ping(request):
    """生存確認"""
    return JsonResponse({"ok": True})
    