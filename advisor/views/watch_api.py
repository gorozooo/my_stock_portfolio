import json
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from advisor.models import WatchEntry

@login_required
@require_GET
def watch_list(request):
    q = (request.GET.get("q") or "").strip()
    cursor = int(request.GET.get("cursor") or 0)
    limit = min(int(request.GET.get("limit") or 20), 50)

    qs = WatchEntry.objects.filter(user=request.user, status=WatchEntry.STATUS_ACTIVE)
    if q:
        qs = qs.filter(Q(ticker__icontains=q) | Q(name__icontains=q))

    total = qs.count()
    rows = qs.order_by("-updated_at")[cursor: cursor+limit]
    items = [{
        "id": r.id,
        "ticker": r.ticker,
        "name": r.name,
        "in_position": r.in_position,
        "reason_summary": r.reason_summary,
        "reason_details": r.reason_details,
        "theme_label": r.theme_label,
        "theme_score": r.theme_score,
        "ai_win_prob": r.ai_win_prob,
        "target_tp": r.target_tp,
        "target_sl": r.target_sl,
        "note": r.note,
        "updated_at": r.updated_at.isoformat(),
    } for r in rows]
    next_cursor = cursor + limit if cursor + limit < total else None

    return JsonResponse({"ok": True, "items": items, "next_cursor": next_cursor})

@login_required
@require_POST
def watch_upsert(request):
    try:
        p = json.loads(request.body.decode("utf-8"))
        tkr = (p.get("ticker") or "").strip()
        if not tkr:
            return HttpResponseBadRequest("ticker required")

        defaults = {}
        for k in ["name","note","reason_summary","reason_details","theme_label","target_tp","target_sl","source"]:
            if k in p: defaults[k] = p[k]
        for fk in ["theme_score","ai_win_prob"]:
            if fk in p: defaults[fk] = float(p[fk])
        if "in_position" in p: defaults["in_position"] = bool(p["in_position"])

        obj, created = WatchEntry.objects.update_or_create(
            user=request.user, ticker=tkr, status=WatchEntry.STATUS_ACTIVE, defaults=defaults
        )
        return JsonResponse({"ok": True, "id": obj.id, "created": created})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

@login_required
@require_POST
def watch_archive(request):
    try:
        p = json.loads(request.body.decode("utf-8"))
        tkr = (p.get("ticker") or "").strip()
        if not tkr:
            return HttpResponseBadRequest("ticker required")
        qs = WatchEntry.objects.filter(user=request.user, ticker=tkr, status=WatchEntry.STATUS_ACTIVE)
        if qs.exists():
            we = qs.first()
            we.status = WatchEntry.STATUS_ARCHIVED
            we.save(update_fields=["status","updated_at"])
            return JsonResponse({"ok": True, "id": we.id})
        return JsonResponse({"ok": True, "id": None})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)