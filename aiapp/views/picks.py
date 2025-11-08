# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from aiapp.services.snapshot import load_snapshot

def _nocache(resp: HttpResponse) -> HttpResponse:
    resp["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = "0"
    return resp

def _fmt_generated(meta_generated: str | None, fallback_ts: float | None) -> str:
    """
    "20251108_200528" -> "2025/11/08 20:05"
    無ければファイルmtimeを使う。両方無ければ空文字。
    """
    # 1) meta.generated_at
    if meta_generated:
        s = str(meta_generated).strip()
        for fmt in ("%Y%m%d_%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.strftime("%Y/%m/%d %H:%M")
            except Exception:
                pass
        # 似た形式でも先頭8桁+_+時刻6桁なら頑張ってパース
        if len(s) >= 15 and s[:8].isdigit() and s[9:15].isdigit():
            try:
                dt = datetime.strptime(s[:15], "%Y%m%d_%H%M%S")
                return dt.strftime("%Y/%m/%d %H:%M")
            except Exception:
                pass
    # 2) ファイルmtime
    if fallback_ts:
        return datetime.fromtimestamp(fallback_ts).strftime("%Y/%m/%d %H:%M")
    return ""

@require_GET
def picks(request: HttpRequest) -> HttpResponse:
    """
    10銘柄カードの表示ビュー（サーバサイド描画）。
    スナップショットは「lite > full > latest > synthetic」の優先順で自動選択。
    """
    data, kind, path = load_snapshot()
    meta = data.get("meta") or {}
    last_text = _fmt_generated(meta.get("generated_at"), path.stat().st_mtime if path else None)
    count = len(data.get("items", []))

    ctx = {
        "items": data.get("items", []),
        "meta": meta,
        "snapshot_kind": kind,                 # 'lite' / 'full' / 'synthetic' / 'latest' / 'missing'
        "snapshot_path": str(path) if path else None,
        "last_updated_text": last_text,        # ← これをテンプレで表示
        "items_count": count,                  # ← 件数
    }
    resp = render(request, "aiapp/picks.html", ctx)
    return _nocache(resp)

@require_GET
def picks_json(request: HttpRequest) -> JsonResponse:
    """
    診断用JSON。UIが更新されない/空に見える時でも、今何を読んでいるか即確認できる。
    """
    data, kind, path = load_snapshot()
    meta = data.get("meta") or {}
    last_text = _fmt_generated(meta.get("generated_at"), path.stat().st_mtime if path else None)
    first3 = [(x.get("code"), x.get("name")) for x in data.get("items", [])[:3]]
    return _nocache(JsonResponse({
        "kind": kind,
        "path": str(path) if path else None,
        "generated_at_raw": meta.get("generated_at"),
        "last_updated_text": last_text,
        "items_count": len(data.get("items", [])),
        "sample": first3,
    }))