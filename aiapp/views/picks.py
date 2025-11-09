# aiapp/views/picks.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from django.conf import settings
from django.http import JsonResponse, Http404
from django.shortcuts import render
from django.utils import timezone

from aiapp.models import StockMaster

PICKS_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "picks"


def _load_latest_path() -> Path | None:
    """
    最新のピックファイルをフォールバック順で解決:
    latest.json → latest_lite.json → latest_full.json → latest_synthetic.json
    """
    candidates = ["latest.json", "latest_lite.json", "latest_full.json", "latest_synthetic.json"]
    for name in candidates:
        p = PICKS_DIR / name
        if p.exists() and p.is_file():
            return p
    return None


def _load_picks() -> Dict[str, Any]:
    p = _load_latest_path()
    if not p:
        return {"meta": {"generated_at": None, "mode": None, "count": 0}, "items": []}

    try:
        data = json.loads(p.read_text())
    except Exception:
        # 破損時は空で返す
        return {"meta": {"generated_at": None, "mode": None, "count": 0}, "items": []}

    # 古い生成物で meta がない事もあるのでケア
    meta = data.get("meta") or {}
    if "count" not in meta:
        meta["count"] = len(data.get("items", []))
    data["meta"] = meta
    return data


def _enrich_with_master(items: List[Dict[str, Any]]) -> None:
    """
    items の code に対して、銘柄名・業種名を付与/補正する。
    - name: 既に name が入ってなければマスタから埋める
    - name_norm: テンプレ側で優先表示したい時のための正規化済み名（当面は name と同じ）
    - sector: 33業種名
    """
    if not items:
        return

    # まとめてマップ化してクエリを 1 回に抑える
    codes = {str(x.get("code", "")).strip() for x in items if x.get("code")}
    masters = {
        sm.code: sm for sm in StockMaster.objects.filter(code__in=codes).only("code", "name", "sector_name")
    }

    for it in items:
        code = str(it.get("code", "")).strip()
        sm = masters.get(code)
        # 名称
        name = it.get("name")
        if not name or name == code:
            it["name"] = sm.name if sm else code
        # 正規化名（いまはそのまま）
        it.setdefault("name_norm", it["name"])
        # セクター
        if not it.get("sector"):
            it["sector"] = (sm.sector_name if sm else None) or "—"


def picks(request):
    # LIVE/DEMO はいまは単純なトグル表示だけ（将来はセッションやクエリで切替）
    mode_param = request.GET.get("mode")
    if mode_param in {"live", "demo"}:
        is_demo = (mode_param == "demo")
    else:
        # 既定は demo 表示（スナップショット優先）
        is_demo = True

    data = _load_picks()
    items = data.get("items", [])
    _enrich_with_master(items)

    # 最終更新表示（()にならないよう防御）
    ts = data.get("meta", {}).get("generated_at")
    try:
        updated_label = ts or timezone.localtime().strftime("%Y-%m-%d %H:%M")
    except Exception:
        updated_label = ts or "-"

    count = data.get("meta", {}).get("count") or len(items)
    updated_label = f"{updated_label}　{count}件 / {(data.get('mode') or data.get('meta',{}).get('mode') or 'lite').lower()}"

    ctx = {
        "items": items,
        "updated_label": updated_label,
        "mode_label": "LIVE/DEMO",
        "is_demo": is_demo,
        # 表示既定（テンプレが参照）
        "lot_size": 100,
        "risk_pct": 0.02,
    }
    return render(request, "aiapp/picks.html", ctx)


def picks_json(request):
    """
    デバッグ/外部確認用: ブラウザから JSON をそのまま確認できる。
    """
    data = _load_picks()
    _enrich_with_master(data.get("items", []))
    if not data:
        raise Http404("no picks")
    return JsonResponse(data, safe=True, json_dumps_params={"ensure_ascii": False, "indent": 2})