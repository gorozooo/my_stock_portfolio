# aiapp/views/picks.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.http import JsonResponse, Http404
from django.shortcuts import render
from django.utils import timezone

from aiapp.models import StockMaster

PICKS_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "picks"


def _load_latest_path() -> Optional[Path]:
    """
    最新のピックJSONをフォールバック順で解決
    """
    for name in ("latest.json", "latest_lite.json", "latest_full.json", "latest_synthetic.json"):
        p = PICKS_DIR / name
        if p.exists() and p.is_file():
            return p
    return None


def _load_picks() -> Dict[str, Any]:
    """
    破損・欠損時も必ず同じスキーマで返す
    """
    path = _load_latest_path()
    base = {"meta": {"generated_at": None, "mode": None, "count": 0}, "items": []}
    if not path:
        return base
    try:
        data = json.loads(path.read_text())
    except Exception:
        return base

    meta = dict(data.get("meta") or {})
    items = list(data.get("items") or [])
    meta.setdefault("mode", data.get("mode"))
    meta.setdefault("count", len(items))
    data = {"meta": meta, "items": items}
    return data


def _enrich_with_master(items: List[Dict[str, Any]]) -> None:
    """
    code → 銘柄名・業種名を補完し、テンプレ表示用の共通キーを作る
      - name / name_norm
      - sector_display（JSON側 sector / sector_name / Master.sector_name の最良値）
      - last_close の None/NaN 防御（テンプレ崩れ防止）
    """
    if not items:
        return

    # まとめて1クエリ
    codes = {str(x.get("code", "")).strip() for x in items if x.get("code")}
    masters = {
        sm.code: sm
        for sm in StockMaster.objects.filter(code__in=codes).only("code", "name", "sector_name")
    }

    for it in items:
        code = str(it.get("code", "")).strip()
        sm = masters.get(code)

        # --- 名称 ---
        name = it.get("name")
        if not name or name == code:
            it["name"] = (sm.name if sm else code) or code
        it.setdefault("name_norm", it["name"])

        # --- 業種（表示用） ---
        # JSON 由来（keysの揺れをケア）
        j_sector = it.get("sector") or it.get("sector_name")
        # Master 由来
        m_sector = getattr(sm, "sector_name", None) if sm else None
        # どれか入っている方を優先
        sector_display = j_sector or m_sector or "—"
        it["sector_display"] = sector_display

        # --- 価格表示の防御 ---
        val = it.get("last_close")
        try:
            it["last_close"] = float(val) if val is not None else None
        except Exception:
            it["last_close"] = None


def _format_updated_label(meta: Dict[str, Any], count: int) -> str:
    """
    generated_at が無い/壊れている場合でも、必ず見栄えするラベルを返す
    例: 2025/11/09 01:23　6件 / SNAPSHOT
    """
    raw_ts = meta.get("generated_at")
    mode = meta.get("mode") or "lite"
    if isinstance(raw_ts, str) and raw_ts.strip():
        ts_label = raw_ts
    else:
        # サーバ現地時刻でフォールバック
        ts_label = timezone.localtime().strftime("%Y/%m/%d %H:%M")
    return f"{ts_label}　{count}件 / {str(mode).upper()}"


def picks(request):
    # LIVE/DEMO は現状表示トグルだけ（将来ここで切替ロジックを入れる）
    mode_q = request.GET.get("mode")
    is_demo = True if mode_q == "demo" else False if mode_q == "live" else True

    data = _load_picks()
    items = data.get("items", [])
    _enrich_with_master(items)

    meta = data.get("meta") or {}
    count = meta.get("count") or len(items)
    updated_label = _format_updated_label(meta, count)

    ctx = {
        "items": items,
        "updated_label": updated_label,  # ← () にならない
        "mode_label": "LIVE/DEMO",
        "is_demo": is_demo,
        # 表示既定
        "lot_size": 100,
        "risk_pct": 0.02,
    }
    return render(request, "aiapp/picks.html", ctx)


def picks_json(request):
    data = _load_picks()
    _enrich_with_master(data.get("items", []))
    if not data:
        raise Http404("no picks")
    return JsonResponse(data, safe=True, json_dumps_params={"ensure_ascii": False, "indent": 2})