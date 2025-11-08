# aiapp/views/picks.py
from __future__ import annotations
import os, json, datetime as dt
from pathlib import Path
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse, Http404
from django.shortcuts import render

MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR  = MEDIA_ROOT / "aiapp" / "picks"

DEFAULT_HORIZON = "short"
DEFAULT_MODE    = "aggressive"

def _latest_path(horizon: str, mode: str) -> Path:
    return PICKS_DIR / f"latest_{horizon}_{mode}.json"

def picks(request: HttpRequest) -> HttpResponse:
    """
    読み取り専用ビュー：事前生成済み latest_*.json を読むだけ。
    例）/aiapp/picks/?h=short&m=aggressive
    """
    h = request.GET.get("h", DEFAULT_HORIZON)
    m = request.GET.get("m", DEFAULT_MODE)

    p = _latest_path(h, m)
    if not p.exists():
        # まだ一度も生成されていない場合は空表示（テンプレ側で“初回生成してください”を出す）
        ctx = {
            "snapshot": {
                "ts": None, "mode": m, "horizon": h, "items": [],
                "universe": None, "version": "picks-v3.1", "metrics": {}
            },
            "updated_at": None,
            "is_live": False,  # DEMO扱い
        }
        return render(request, "aiapp/picks.html", ctx)

    with open(p, "r", encoding="utf-8") as f:
        snap = json.load(f)

    # 最終更新の表示
    try:
        ts = dt.datetime.fromisoformat(snap.get("ts")).astimezone(dt.timezone(dt.timedelta(hours=9)))
        updated_at = ts.strftime("%Y/%m/%d(%a) %H:%M")
    except Exception:
        updated_at = None

    # 一旦 “LIVE” バッジは「ファイル更新から15分以内」をLIVE扱い
    is_live = False
    try:
        mtime = dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=9)))
        is_live = (dt.datetime.now(dt.timezone(dt.timedelta(hours=9))) - mtime) <= dt.timedelta(minutes=15)
    except Exception:
        pass

    ctx = {
        "snapshot": snap,
        "updated_at": updated_at,
        "is_live": is_live,
        "h": h, "m": m,
    }
    return render(request, "aiapp/picks.html", ctx)