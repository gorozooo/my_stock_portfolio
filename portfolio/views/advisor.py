# portfolio/views/advisor.py
from __future__ import annotations
from django.http import JsonResponse, HttpRequest, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.db.models import Max

from ..models_advisor import AdviceItem, AdviceSession


def latest_session_items(request: HttpRequest):
    """（任意）最新セッションの提案をJSONで返す"""
    sess = AdviceSession.objects.order_by("-created_at").first()
    if not sess:
        return JsonResponse({"items": [], "session_id": None})
    items = [
        {"id": it.id, "kind": it.kind, "message": it.message, "score": it.score, "taken": it.taken}
        for it in AdviceItem.objects.filter(session=sess).order_by("-score", "-id")
    ]
    return JsonResponse({"items": items, "session_id": sess.id})


@require_POST
def toggle_taken(request: HttpRequest, item_id: int):
    """✅/⛔️ のトグル。成功時 {ok:true, taken:bool} を返す"""
    if not str(item_id).isdigit():
        return HttpResponseBadRequest("invalid id")
    item = get_object_or_404(AdviceItem, id=item_id)
    item.taken = not item.taken
    item.save(update_fields=["taken"])
    return JsonResponse({"ok": True, "taken": item.taken, "id": item.id})


def has_sessions(request: HttpRequest):
    """ホームで“提案が無い”状態を素早く判定したい時に使える軽量API"""
    exists = AdviceSession.objects.aggregate(n=Max("id")).get("n") is not None
    return JsonResponse({"has": exists})