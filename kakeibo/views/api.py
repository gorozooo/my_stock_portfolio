# =========================================
# [FILE] api.py
# [PATH] kakeibo/views/api.py
#
# このファイルは何？
# owner（HOUSE/B/G）に応じて
# - カード一覧（Account.kind=CARD）
# - 口座一覧（Account.kind=ACCOUNT）
# を返すシンプルAPI。
# JS側がこの結果で「候補を絞る」ために使う。
# =========================================

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse

from .permissions import kakeibo_access_required
from ..models import Account


def _bad(msg: str, status: int = 400):
    return JsonResponse({"ok": False, "error": msg}, status=status)


@login_required
def api_cards(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    owner = (request.GET.get("owner") or "").strip()
    if owner not in ("HOUSE", "B", "G"):
        return _bad("owner must be one of HOUSE/B/G")

    qs = Account.objects.filter(kind="CARD", owner=owner).order_by("id")
    items = [{"id": a.id, "name": a.name} for a in qs]
    return JsonResponse({"ok": True, "items": items})


@login_required
def api_accounts(request):
    if not kakeibo_access_required(request.user):
        raise PermissionDenied("You do not have access to kakeibo.")

    owner = (request.GET.get("owner") or "").strip()
    if owner not in ("HOUSE", "B", "G"):
        return _bad("owner must be one of HOUSE/B/G")

    qs = Account.objects.filter(kind="ACCOUNT", owner=owner).order_by("id")
    items = [{"id": a.id, "name": a.name} for a in qs]
    return JsonResponse({"ok": True, "items": items})