# portfolio/views/advisor.py
from __future__ import annotations
from django.http import JsonResponse, HttpRequest, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.db.models import Max

from ..models_advisor import AdviceItem, AdviceSession
from ..ab import log_event

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
    ...
    item.taken = not item.taken
    item.save(update_fields=["taken"])

    # A/B ログ（Cookie or user.id を identity に使う）
    try:
        identity = f"user:{request.user.id}" if (getattr(request, "user", None) and request.user.is_authenticated) else request.COOKIES.get("abid") or "anon"
        # variant は assignment から見るのが理想だが、軽量化のためテンプレ側 hidden から送る案でもOK。
        # ここでは簡易に 'A' としておき、テンプレで hidden input[name=ab_variant] をPOSTすればそれを使う実装にしても良い
        variant = request.POST.get("ab_variant", "A")
        log_event("ai_advisor_layout", identity, variant, "click_check", {"item_id": item.id, "taken": item.taken})
    except Exception:
        pass

    return JsonResponse({"ok": True, "taken": item.taken, "id": item.id})


def has_sessions(request: HttpRequest):
    """ホームで“提案が無い”状態を素早く判定したい時に使える軽量API"""
    exists = AdviceSession.objects.aggregate(n=Max("id")).get("n") is not None
    return JsonResponse({"has": exists})