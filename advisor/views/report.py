from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone, timedelta
from django.conf import settings
from django.http import HttpResponse, HttpResponseNotFound

JST = timezone(timedelta(hours=9))

def daily_report(request, yyyymmdd: str = "today"):
    try:
        if yyyymmdd == "today":
            d = datetime.now(JST).date().isoformat()
        else:
            # 簡易バリデーション
            _ = datetime.strptime(yyyymmdd, "%Y-%m-%d")
            d = yyyymmdd
    except Exception:
        return HttpResponseNotFound("invalid date")

    root = Path(getattr(settings, "MEDIA_ROOT", "."))
    path = root / "advisor" / "reports" / f"{d}.html"
    if not path.exists():
        return HttpResponseNotFound("report not found")

    html = path.read_text(encoding="utf-8")
    return HttpResponse(html, content_type="text/html; charset=utf-8")