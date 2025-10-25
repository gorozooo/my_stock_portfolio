import json
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from advisor.models import WatchEntry
from django.db.models import Q

@login_required
@require_GET
def watch_list(request):
    q = (request.GET.get("q") or "").strip()
    cursor = int(request.GET.get("cursor") or 0)  # 単純cursor（次のoffset）
    limit = min(int(request.GET.get("limit") or 20), 50)

    qs = WatchEntry.objects.filter(user=request.user, status=WatchEntry.STATUS_ACTIVE)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    total = qs.count()
    items = list(qs.order_by("-updated_at")[cursor: cursor+limit].values(
        "id","ticker","name","note","in_position","updated_at"
    ))
    next_cursor = cursor + limit if cursor + limit < total else None

    return JsonResponse({"ok": True, "items": items, "next_cursor": next_cursor})

@login_required
@require_POST
def watch_upsert(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        ticker = (payload.get("ticker") or "").strip()
        name = (payload.get("name") or "").strip()
        note = (payload.get("note") or "").strip()
        in_position = bool(payload.get("in_position", False))

        if not ticker:
            return HttpResponseBadRequest("ticker required")

        obj, created = WatchEntry.objects.update_or_create(
            user=request.user, ticker=ticker, status=WatchEntry.STATUS_ACTIVE,
            defaults={"name": name, "note": note, "in_position": in_position}
        )
        return JsonResponse({"ok": True, "id": obj.id, "created": created})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

@login_required
@require_POST
def watch_archive(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        ticker = (payload.get("ticker") or "").strip()
        if not ticker:
            return HttpResponseBadRequest("ticker required")

        # active を archived に（同一tickerのみ）
        qs = WatchEntry.objects.filter(user=request.user, ticker=ticker, status=WatchEntry.STATUS_ACTIVE)
        if qs.exists():
            obj = qs.first()
            obj.status = WatchEntry.STATUS_ARCHIVED
            obj.save(update_fields=["status","updated_at"])
            return JsonResponse({"ok": True, "id": obj.id})
        return JsonResponse({"ok": True, "id": None})  # 既に無ければOK
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)