# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from pathlib import Path
from datetime import timedelta
from typing import Dict

from django.http import JsonResponse, HttpRequest, HttpResponseNotAllowed, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.conf import settings
from django.utils import timezone

from ..models_advisor import AdviceItem

# === 学習ロジック（management command と同等の軽量版） ===
def _learn_and_write(days: int, out_relpath: str = "media/advisor/policy.json",
                     bias: float = 1.0, clip_low: float = 0.80, clip_high: float = 1.30) -> Dict:
    since = timezone.now() - timedelta(days=days)
    qs = (
        AdviceItem.objects
        .filter(created_at__gte=since)
        .values("kind", "taken")
    )

    # kind ごとに集計
    kinds = {}
    for row in qs:
        k = row["kind"] or "REBALANCE"
        taken = 1 if row["taken"] else 0
        acc = kinds.setdefault(k, {"n": 0, "taken": 0})
        acc["n"] += 1
        acc["taken"] += taken

    # ラプラス平滑 + 平均=1.0へ正規化 → クリップ → バイアス
    if not kinds:
        raw_weight = {
            "REBALANCE": 1.0,
            "ADD_CASH": 1.0,
            "TRIM_WINNERS": 1.0,
            "CUT_LOSERS": 1.0,
            "REDUCE_MARGIN": 1.0,
        }
    else:
        raw_weight = {}
        for k, v in kinds.items():
            n = v["n"]; t = v["taken"]
            p = (t + 1) / (n + 2) if n >= 0 else 0.5
            raw_weight[k] = p

    avg = sum(raw_weight.values()) / max(len(raw_weight), 1)
    normed = {k: (v / (avg or 1.0)) for k, v in raw_weight.items()}
    kind_weight = {k: max(clip_low, min(clip_high, bias * w)) for k, w in normed.items()}

    payload = {
        "version": 1,
        "updated_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bias": bias,
        "origin": f"web:{days}d",
        "kind_weight": kind_weight,
    }

    base = getattr(settings, "MEDIA_ROOT", "") or settings.BASE_DIR
    out_path = Path(base) / out_relpath if not Path(out_relpath).is_absolute() else Path(out_relpath)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"saved": str(out_path), "kinds": len(kind_weight), "days": days}

@require_POST
@csrf_protect
def advisor_learn_now(request: HttpRequest):
    if not request.user.is_authenticated or not request.user.is_staff:
        return HttpResponseForbidden("staff only")

    try:
        days = int(request.POST.get("days", "90"))
    except Exception:
        days = 90

    # 任意カスタム（必要ならPOSTで受ける）
    bias = float(request.POST.get("bias", "1.0"))
    clip_low = float(request.POST.get("clip_low", "0.80"))
    clip_high = float(request.POST.get("clip_high", "1.30"))

    try:
        res = _learn_and_write(days=days, bias=bias, clip_low=clip_low, clip_high=clip_high)
        return JsonResponse({"ok": True, **res})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)