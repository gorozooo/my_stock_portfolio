# portfolio/api/positions.py
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from ..models import Position

@login_required
@require_POST
def add_position(request):
    """
    trendページの［注文プランに追加］から叩くエンドポイント。
    入力:
      - ticker, side(LONG/SHORT), entry, stop, qty
      - targets[] (0..n)
      - sync_holding (将来拡張用, bool)
      - account_type ("margin"|"cash"|"nisa") ← 使わなくても受け取ってOK
    """
    try:
        data = request.POST
        ticker = (data.get("ticker") or "").strip()
        side   = (data.get("side") or "LONG").upper()
        entry  = float(data.get("entry")) if data.get("entry") else None
        stop   = float(data.get("stop"))  if data.get("stop")  else None
        qty    = int(float(data.get("qty") or 0))
        targets = [float(x) for x in request.POST.getlist("targets[]") if x]

        if not (ticker and side in ("LONG", "SHORT") and entry and stop and qty > 0):
            return JsonResponse({"ok": False, "error": "invalid params"}, status=400)

        pos = Position.objects.create(
            user=request.user,
            ticker=ticker,
            side=side,
            entry_price=entry,
            stop_price=stop,
            qty=qty,
            targets=targets,
        )
        return JsonResponse({"ok": True, "id": pos.id})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)