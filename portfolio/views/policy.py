# -*- coding: utf-8 -*-
from __future__ import annotations
import os, glob, json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.shortcuts import render

# ---------- ユーティリティ ----------
def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        # "2025-01-02T03:04:05+09:00" / "2025-01-02T03:04:05Z" 両対応
        s = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _overall_score(policy: Dict) -> Optional[float]:
    """
    category.avg_improve を count×confidence で加重平均。
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
        avg  = _safe_float(v.get("avg_improve"), 0.0)
        ww = max(0.0, cnt * conf)
        s += ww * avg
        w += ww
    return (s / w) if w > 0 else None

def _weighted_winrate(policy: Dict) -> Optional[float]:
    """
    win_rate(0..1) を count で加重平均。%表示はテンプレ側で。
    """
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
    MEDIA_ROOT 配下を探す：
      - advisor/history/policy_*.json（推奨）
      - advisor/policy.json（単発）
    を時系列ラベルつきで返す。
    """
    media_root = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    hist_dir = os.path.join(media_root, "advisor", "history")
    main_file = os.path.join(media_root, "advisor", "policy.json")

    paths: List[str] = []
    if os.path.isdir(hist_dir):
        paths.extend(glob.glob(os.path.join(hist_dir, "policy_*.json")))

    items: List[Dict] = []
    for p in sorted(paths):
        try:
            with open(p, "r", encoding="utf-8") as f:
                items.append(json.load(f))
        except Exception:
            continue

    if not items and os.path.exists(main_file):
        try:
            with open(main_file, "r", encoding="utf-8") as f:
                items = [json.load(f)]
        except Exception:
            pass

    return items

# ---------- View ----------
def policy_history(request):
    """
    policy 履歴を時系列で可視化し、“一番良かった週”を表示する。
    """
    hist = _load_history()
    points = []
    for obj in hist:
        dt = _parse_iso(obj.get("generated_at")) or _parse_iso(obj.get("updated_at"))
        label = dt.strftime("%Y-%m-%d") if dt else (obj.get("generated_at") or obj.get("updated_at") or "unknown")
        score = _overall_score(obj)
        winrt = _weighted_winrate(obj)  # 0..1
        horizon = obj.get("horizon_days") or obj.get("horizon") or 7
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