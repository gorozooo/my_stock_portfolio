from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET
from advisor.models import WatchEntry

@login_required
@require_GET
def watch_list(request):
    """
    ウォッチリストの取得
    ACTIVE のみ返却し、キャッシュを完全無効化
    """
    q = (request.GET.get("q") or "").strip()
    limit = int(request.GET.get("limit", 20))
    cursor = int(request.GET.get("cursor", 0)) if request.GET.get("cursor") else 0

    qs = WatchEntry.objects.filter(user=request.user, status=WatchEntry.STATUS_ACTIVE)
    if q:
        qs = qs.filter(ticker__icontains=q) | qs.filter(name__icontains=q)
    qs = qs.order_by("-updated_at")[cursor:cursor + limit + 1]

    items = []
    for w in qs[:limit]:
        items.append({
            "ticker": w.ticker,
            "name": w.name,
            "note": w.note,
            "reason_summary": w.reason_summary,
            "in_position": w.in_position,
            "theme_label": getattr(w, "theme_label", ""),
            "theme_score": getattr(w, "theme_score", 0),
            "ai_win_prob": getattr(w, "ai_win_prob", 0),
            "target_tp": getattr(w, "target_tp", ""),
            "target_sl": getattr(w, "target_sl", ""),
            "reason_details": getattr(w, "reason_details", []),
        })
    next_cursor = cursor + limit if len(qs) > limit else None

    resp = JsonResponse({"ok": True, "items": items, "next_cursor": next_cursor})
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp