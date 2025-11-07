# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import os
from datetime import datetime
from typing import List, Dict

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample, score_and_detail

# ===== 画面の既定（ユーザー指定が無ければ短期×攻め×淡々） =====
DEFAULT_HORIZON = "short"        # short / mid / long
DEFAULT_MODE    = "aggressive"   # aggressive / normal / defensive
DEFAULT_TONE    = "calm"         # calm(=淡々), neutral, positive（今は表示テキストの演色に使う予定）

# ユニバース最大件数（重すぎ防止）。まずは先頭Nだけスキャンして上位10件。
UNIVERSE_LIMIT = int(getattr(settings, "AIAPP_UNIVERSE_LIMIT", 400))

# スナップショット（任意保存）。無ければオンデマンドで計算して表示する方針。
SNAPSHOT_DIR = getattr(settings, "AIAPP_SNAPSHOT_DIR", "media/aiapp/picks")
SAVE_SNAPSHOT = bool(getattr(settings, "AIAPP_SAVE_SNAPSHOT", False))  # Trueにすると保存もする


def _session_get(request: HttpRequest, key: str, default=None):
    return request.session.get(key, default)


def _session_set(request: HttpRequest, key: str, value):
    request.session[key] = value
    request.session.modified = True


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _load_snapshot_latest(kind: str = "live") -> List[Dict]:
    """スナップショット読み（任意）。見つからなければ空配列。"""
    try:
        _ensure_dir(SNAPSHOT_DIR)
        files = sorted(
            [os.path.join(SNAPSHOT_DIR, f) for f in os.listdir(SNAPSHOT_DIR) if f.endswith(f"_{kind}.json")],
            key=lambda p: os.path.getmtime(p)
        )
        if not files:
            return []
        with open(files[-1], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_snapshot(items: List[Dict], kind: str = "live"):
    if not SAVE_SNAPSHOT:
        return
    _ensure_dir(SNAPSHOT_DIR)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SNAPSHOT_DIR, f"{ts}_{kind}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
    except Exception:
        pass


def _build_live_items(horizon: str, mode: str, topn: int = 10) -> List[Dict]:
    """
    ユニバースから上位TopNをオンデマンドで構築。
    保存物が無くてもこれで画面に出す。
    """
    qs = StockMaster.objects.all().order_by("code").values("code", "name")[:UNIVERSE_LIMIT]
    got = []
    for row in qs:
        code, name = row["code"], row["name"]
        df = get_prices(code, 180)
        if len(df) < 60:
            continue
        feat = compute_features(df)
        detail = score_and_detail(feat, mode=mode, horizon=horizon)
        got.append({
            "code": code,
            "name": name,
            "score": round(detail.score, 3),
            "stars": detail.stars,
            "reasons": detail.reasons,
            "rules_hit": detail.rules_hit,
            # 価格系の簡易ガイド（目安）
            "last_close": float(df["Close"].iloc[-1]),
        })
    # スコア順で上位TopN
    got.sort(key=lambda x: x["score"], reverse=True)
    return got[:topn]


def picks(request: HttpRequest) -> HttpResponse:
    """
    AI PICKS 画面：
      - ?mode=live|demo （既定 live）
      - ?reset=1      （セッション初期化）
      - ?horizon=short|mid|long
      - ?style=aggressive|normal|defensive
    """
    # --- セッション初期化 ---
    if request.GET.get("reset") == "1":
        for k in ["aiapp_mode", "aiapp_horizon", "aiapp_style", "aiapp_tone"]:
            if k in request.session:
                del request.session[k]

    # --- モード決定（明示 > セッション > 既定）---
    mode = request.GET.get("mode") or _session_get(request, "aiapp_mode", "live")
    if mode not in ("live", "demo"):
        mode = "live"
    _session_set(request, "aiapp_mode", mode)

    # --- 期間/スタイル/トーン決定 ---
    horizon = request.GET.get("horizon") or _session_get(request, "aiapp_horizon", DEFAULT_HORIZON)
    if horizon not in ("short", "mid", "long"):
        horizon = DEFAULT_HORIZON
    _session_set(request, "aiapp_horizon", horizon)

    style = request.GET.get("style") or _session_get(request, "aiapp_style", DEFAULT_MODE)
    if style not in ("aggressive", "normal", "defensive"):
        style = DEFAULT_MODE
    _session_set(request, "aiapp_style", style)

    tone = request.GET.get("tone") or _session_get(request, "aiapp_tone", DEFAULT_TONE)
    _session_set(request, "aiapp_tone", tone)

    # --- データロード ---
    items: List[Dict]
    is_demo = (mode == "demo")

    if is_demo:
        # DEMO明示時のみサンプルを読み込み（無ければ空）
        items = _load_snapshot_latest("demo")
    else:
        # まずスナップショット（任意）を探す
        items = _load_snapshot_latest("live")
        if not items:
            # 無ければオンデマンドで構築（← ここが今回の肝）
            items = _build_live_items(horizon=horizon, mode=style, topn=10)
            _save_snapshot(items, "live")

    # --- 画面ヘッダ（更新時刻/LIVE or DEMO） ---
    updated_at = datetime.now()
    mode_label = "DEMO" if is_demo else "LIVE"
    updated_label = updated_at.strftime("%Y/%m/%d(%a) %H:%M")

    context = {
        "updated_label": updated_label,
        "mode_label": mode_label,
        "is_demo": is_demo,
        "items": items,
        "horizon": horizon,
        "style": style,
        "tone": tone,
    }
    return render(request, "aiapp/picks.html", context)