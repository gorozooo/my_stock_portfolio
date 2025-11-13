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

# JPX 33業種（コード→日本語名）
JPX_SECTOR_MAP: Dict[str, str] = {
    "005": "水産・農林業", "010": "鉱業", "015": "建設業", "020": "食料品", "025": "繊維製品",
    "030": "パルプ・紙", "035": "化学", "040": "医薬品", "045": "石油・石炭製品", "050": "ゴム製品",
    "055": "ガラス・土石製品", "060": "鉄鋼", "065": "非鉄金属", "070": "金属製品", "075": "機械",
    "080": "電気機器", "085": "輸送用機器", "090": "精密機器", "095": "その他製品",
    "100": "電気・ガス業",
    "105": "陸運業", "110": "海運業", "115": "空運業", "120": "倉庫・運輸関連業",
    "125": "情報・通信業",
    "130": "卸売業", "135": "小売業",
    "140": "銀行業", "145": "証券・商品先物取引業", "150": "保険業", "155": "その他金融業",
    "160": "不動産業",
    "165": "サービス業",
}


def _load_latest_path() -> Optional[Path]:
    """最新ピックJSONの実体ファイルをフォールバック順で解決"""
    for name in ("latest.json", "latest_lite.json", "latest_full.json", "latest_synthetic.json"):
        p = PICKS_DIR / name
        if p.exists() and p.is_file():
            return p
    return None


def _load_picks() -> Dict[str, Any]:
    """壊れていてもスキーマを崩さず返す"""
    path = _load_latest_path()
    base = {"meta": {"generated_at": None, "mode": None, "count": 0}, "items": [], "_path": None}
    if not path:
        return base
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {}
    meta = dict(data.get("meta") or {})
    items = list(data.get("items") or [])
    # 互換：トップレベルに mode がある旧構造も拾う
    meta.setdefault("mode", data.get("mode"))
    meta.setdefault("count", len(items))
    data = {"meta": meta, "items": items, "_path": str(path)}
    return data


def _is_etf(code: str) -> bool:
    """ざっくりETF判定（先頭1xxxが多い）。厳密化は後でOK"""
    try:
        return code and code[0] == "1"
    except Exception:
        return False


def _sector_from_master(sm: Optional[StockMaster]) -> Optional[str]:
    if not sm:
        return None
    # sector_name が入っていれば最優先
    if sm.sector_name:
        return sm.sector_name
    # sector_code → 名称へ
    if sm.sector_code and sm.sector_code in JPX_SECTOR_MAP:
        return JPX_SECTOR_MAP[sm.sector_code]
    return None


def _enrich_with_master(data: Dict[str, Any]) -> None:
    """itemsを銘柄名/業種/価格の表示用に正規化"""
    items: List[Dict[str, Any]] = list(data.get("items") or [])
    if not items:
        return

    codes = {str(x.get("code", "")).strip() for x in items if x.get("code")}
    masters = {
        sm.code: sm
        for sm in StockMaster.objects.filter(code__in=codes).only("code", "name", "sector_name", "sector_code")
    }

    for it in items:
        code = str(it.get("code", "")).strip()
        sm = masters.get(code)

        # name
        name = it.get("name")
        if not name or name == code:
            it["name"] = (sm.name if sm else code) or code
        it.setdefault("name_norm", it["name"])

        # sector display（フォールバック順）
        sector_json = it.get("sector") or it.get("sector_name")
        sector_mst = _sector_from_master(sm)
        if _is_etf(code):
            sector_disp = "ETF/ETN"
        else:
            sector_disp = sector_json or sector_mst or "業種不明"
        it["sector_display"] = sector_disp

        # last_close 防御
        val = it.get("last_close")
        try:
            it["last_close"] = float(val) if val is not None else None
        except Exception:
            it["last_close"] = None


def _format_updated_label(meta: Dict[str, Any], path_str: Optional[str], count: int) -> str:
    """
    表示用の最終更新ラベル：
      1) meta.generated_at があればそれを表示
      2) 無ければ JSONファイルの mtime を表示
    例: 2025/11/09 01:23　6件 / FORCE_LITE
    """
    mode = meta.get("mode") or "lite"
    raw_ts = (meta.get("generated_at") or "").strip() if isinstance(meta.get("generated_at"), str) else None

    if raw_ts:
        ts_label = raw_ts
    else:
        # ファイル mtime にフォールバック
        if path_str:
            p = Path(path_str)
            if p.exists():
                ts_label = timezone.localtime(
                    timezone.make_aware(
                        timezone.datetime.fromtimestamp(p.stat().st_mtime)
                    )
                ).strftime("%Y/%m/%d %H:%M")
            else:
                ts_label = timezone.localtime().strftime("%Y/%m/%d %H:%M")
        else:
            ts_label = timezone.localtime().strftime("%Y/%m/%d %H:%M")
    return f"{ts_label}　{count}件 / {str(mode).upper()}"


def picks(request):
    # LIVE/DEMO 切替（将来ロジック拡張）
    qmode = request.GET.get("mode")
    is_demo = True if qmode == "demo" else False if qmode == "live" else True

    data = _load_picks()
    _enrich_with_master(data)

    meta = data.get("meta") or {}
    items = list(data.get("items") or [])
    count = meta.get("count") or len(items)
    updated_label = _format_updated_label(meta, data.get("_path"), count)

    # ★ sizing_service から出した lot_size / risk_pct を1件目から拾う
    lot_size = 100
    risk_pct = 1.0
    if items:
        base = items[0]
        try:
            if base.get("lot_size"):
                lot_size = int(base.get("lot_size"))
        except Exception:
            pass
        try:
            if base.get("risk_pct") is not None:
                risk_pct = float(base.get("risk_pct"))
        except Exception:
            pass

    ctx = {
        "items": items,
        "updated_label": updated_label,
        "mode_label": "LIVE/DEMO",
        "is_demo": is_demo,
        "lot_size": lot_size,
        "risk_pct": risk_pct,
    }
    return render(request, "aiapp/picks.html", ctx)


def picks_json(request):
    data = _load_picks()
    _enrich_with_master(data)
    if not data:
        raise Http404("no picks")
    # 内部用メタは出さない
    data.pop("_path", None)
    return JsonResponse(data, safe=True, json_dumps_params={"ensure_ascii": False, "indent": 2})