# aiapp/views/api.py
from __future__ import annotations
import os, subprocess, time
from pathlib import Path
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST

from django.conf import settings

PY = Path(getattr(settings, "VENV_PY", getattr(settings, "PYTHON_BIN", "python")))

@require_POST
def picks_rebuild(request: HttpRequest):
    """
    非同期で manage.py picks_build を起動するだけの軽量API。
    多重起動は manage 側のロックで回避。
    """
    h = request.POST.get("h", "short")
    m = request.POST.get("m", "aggressive")

    try:
        # 非同期起動
        subprocess.Popen(
            [str(PY), "manage.py", "picks_build", "--horizon", h, "--mode", m],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return JsonResponse({"ok": True, "msg": "accepted", "h": h, "m": m})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)