# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import pathlib
from datetime import datetime

from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.conf import settings

PICKS_DIR = pathlib.Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"

def _load_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}

def _pick_latest() -> tuple[list[dict], str, str]:
    """
    優先順位:
      1) latest_lite.json（日中の差分）
      2) latest_full.json（夜間の完全/スナップショット）
      3) latest.json（互換）
    """
    order = ["latest_lite.json", "latest_full.json", "latest.json"]
    for name in order:
        p = PICKS_DIR / name
        if p.exists():
            d = _load_json(p)
            items = d.get("items", [])
            mode  = d.get("mode", "SNAPSHOT")
            ts    = d.get("updated_at") or ""
            return items, mode, ts
    return [], "DEMO", ""

def picks(request):
    items, mode, ts = _pick_latest()

    ctx = {
        "items": items,
        "mode_label": mode,
        "updated_label": ts if ts else "—",
        "is_demo": (mode == "DEMO"),
        "lot_size": 100,
        "risk_pct": 0.02,
    }

    # 親しみトーンの理由テキスト整形
    for it in ctx["items"]:
        r = it.get("reasons", {}) or {}
        lines = []
        if "trend" in r:
            try:
                lines.append(f"直近の流れは+{float(r['trend']):.1f}%で上向き。勢いは続きやすいムード。")
            except Exception:
                pass
        if "rs" in r:
            try:
                lines.append(f"指数比の相対強度も+{float(r['rs']):.1f}%と健闘。ベンチよりやや強め。")
            except Exception:
                pass
        if "vol_signal" in r:
            try:
                lines.append(f"出来高は平均比×{float(r['vol_signal']):.2f}。注目度が高まっている可能性。")
            except Exception:
                pass
        if "atr" in r:
            try:
                lines.append(f"ボラはATR≈{float(r['atr']):.1f}。過度ではなく扱いやすいレンジ。")
            except Exception:
                pass
        it["reasons_text"] = lines

        # 表示フォーマット補正
        try:
            it["score_100"] = max(0, min(100, int(round(it.get("score_100", 0)))))
        except Exception:
            it["score_100"] = 0
        try:
            it["stars"] = max(1, min(5, int(it.get("stars", 1))))
        except Exception:
            it["stars"] = 1

    return render(request, "aiapp/picks.html", ctx)

def picks_json(request):
    """最新ピックをJSONで返す（フロント/監視用）"""
    items, mode, ts = _pick_latest()
    resp = JsonResponse({
        "items": items,
        "mode": mode,
        "updated_at": ts,
    }, json_dumps_params={"ensure_ascii": False})
    # 強制ノーキャッシュ（スマホSafari対策）
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    return resp