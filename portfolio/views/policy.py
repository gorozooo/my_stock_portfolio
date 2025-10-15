# -*- coding: utf-8 -*-
from __future__ import annotations
import os, glob, json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.shortcuts import render

# ------- 集計ロジック -------
def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _overall_score(policy: Dict) -> Optional[float]:
    """
    category の avg_improve を confidence×count で重み付け平均。
    値が大きいほど“その週の改善度が良かった”とみなす。
    """
    cats = policy.get("category") or {}
    if not cats:
        return None
    s = 0.0
    w = 0.0
    for v in cats.values():
        cnt = _safe_float(v.get("count"), 0.0)
        conf = _safe_float(v.get("confidence"), 0.0)
        avg = _safe_float(v.get("avg_improve"), 0.0)
        ww = max(0.0, cnt * conf)
        s += ww * avg
        w += ww
    return (s / w) if w > 0 else None

def _weighted_winrate(policy: Dict) -> Optional[float]:
    """win_rate を count で重み付け平均。"""
    cats = policy.get("category") or {}
    if not cats:
        return None
    s = 0.0
    w = 0.0
    for v in cats.values():
        cnt = _safe_float(v.get("count"), 0.0)
        win = _safe_float(v.get("win_rate"), 0.0)
        s += cnt * win
        w += cnt
    return (s / w) if w > 0 else None

def _load_history() -> List[Dict]:
    """
    media/advisor/history/policy_YYYY-MM-DD.json を時系列で読み込む。
    なければ media/advisor/policy.json を単点として返す。
    """
    media_root = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    hist_dir = os.path.join(media_root, "media/advisor/history")  # MEDIA_ROOT/media/... にも対応
    alt_hist_dir = os.path.join(media_root, "advisor/history")    # MEDIA_ROOT/advisor/... にも対応
    main_file = os.path.join(media_root, "media/advisor/policy.json")
    alt_main_file = os.path.join(media_root, "advisor/policy.json")

    paths = []
    for base in (hist_dir, alt_hist_dir):
        if os.path.isdir(base):
            paths.extend(glob.glob(os.path.join(base, "policy_*.json")))

    items: List[Dict] = []
    for p in sorted(paths):
        try:
            with open(p, "r", encoding="utf-8") as f:
                obj = json.load(f)
            items.append(obj)
        except Exception:
            continue

    if not items:
        # 単発ファイルの救済
        for p in (main_file, alt_main_file):
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                    items = [obj]
                except Exception:
                    pass
                break
    return items

# ------- View -------
def policy_history(request):
    """
    policy 履歴を時系列で可視化＋“一番良かった週”を表示。
    """
    hist = _load_history()
    points = []
    for obj in hist:
        gen = obj.get("generated_at")
        try:
            # 2025-01-02T03:04:05+09:00 など想定
            dt = datetime.fromisoformat(gen.replace("Z", "+00:00")) if gen else None
        except Exception:
            dt = None
        label = dt.strftime("%Y-%m-%d") if dt else (gen or "unknown")
        score = _overall_score(obj)
        winrt = _weighted_winrate(obj)
        horizon = obj.get("horizon_days", 7)
        points.append(dict(label=label, score=score, winrt=winrt, horizon=horizon, raw=obj))

    # ベスト週（score 最大）
    best_idx = None
    best_val = None
    for i, p in enumerate(points):
        if p["score"] is None:
            continue
        if (best_val is None) or (p["score"] > best_val):
            best_val = p["score"]
            best_idx = i

    ctx = dict(
        points=points,
        best_idx=best_idx,
        best=points[best_idx] if best_idx is not None else None,
        has_data=len(points) > 0,
    )
    return render(request, "advisor_policy.html", ctx)