from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from ..models import Position

@login_required
def add_position(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"})
    data = request.POST
    pos = Position.objects.create(
        user=request.user,
        ticker=data["ticker"],
        name=data.get("name", ""),
        side=data["side"],
        entry_price=float(data["entry"]),
        stop_price=float(data["stop"]),
        qty=int(data["qty"]),
        targets=[float(x) for x in data.getlist("targets[]", [])],
    )
    return JsonResponse({"ok": True, "id": pos.id})