# -*- coding: utf-8 -*-
from __future__ import annotations
import os, glob, json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.utils import timezone
from django.core.management import call_command

# ========= 小ユーティリティ =========
def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _overall_score(policy: Dict) -> Optional[float]:
    """category.avg_improve を confidence×count で重み付け平均"""
    cats = policy.get("category") or {}
    if not cats:
        return None
    s = w = 0.0
    for v in cats.values():
        cnt = _safe_float(v.get("count"), 0.0)
        conf = _safe_float(v.get("confidence"), 0.0)
        avg = _safe_float(v.get("avg_improve"), 0.0)
        ww = max(0.0, cnt * conf)
        s += ww * avg
        w += ww
    return (s / w) if w > 0 else None

def _weighted_winrate(policy: Dict) -> Optional[float]:
    """win_rate の count 加重平均"""
    cats = policy.get("category") or {}
    if not cats:
        return None
    s = w = 0.0
    for v in cats.values():
        cnt = _safe_float(v.get("count"), 0.0)
        win = _safe_float(v.get("win_rate"), 0.0)
        s += cnt * win
        w += cnt
    return (s / w) if w > 0 else None

def _media_candidates() -> List[str]:
    """
    MEDIA_ROOT の配下における候補ディレクトリを返す。
    - <MEDIA_ROOT>/advisor/history
    - <MEDIA_ROOT>/media/advisor/history   （環境によってこちらに置いているケースがあった）
    """
    mr = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    return [
        os.path.join(mr, "advisor", "history"),
        os.path.join(mr, "media", "advisor", "history"),
    ]

def _single_main_candidates() -> List[str]:
    """単発ファイル policy.json の候補"""
    mr = getattr(settings, "MEDIA_ROOT", "") or os.getcwd()
    return [
        os.path.join(mr, "advisor", "policy.json"),
        os.path.join(mr, "media", "advisor", "policy.json"),
    ]

def _load_history() -> List[Dict]:
    """policy_YYYY-MM-DD.json を新しい順で読み込み。無ければ単発 policy.json を読む。"""
    paths: List[str] = []
    for base in _media_candidates():
        if os.path.isdir(base):
            paths.extend(glob.glob(os.path.join(base, "policy_*.json")))
    items: List[Dict] = []
    for p in sorted(paths):
        try:
            with open(p, "r", encoding="utf-8") as f:
                items.append(json.load(f))
        except Exception:
            continue
    if not items:
        for p in _single_main_candidates():
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        items = [json.load(f)]
                except Exception:
                    pass
                break
    return items

def _points_from_history(hist: List[Dict]) -> Tuple[List[Dict], Optional[int]]:
    points: List[Dict] = []
    for obj in hist:
        gen = obj.get("generated_at") or obj.get("updated_at")
        dt = None
        if isinstance(gen, str):
            try:
                dt = datetime.fromisoformat(gen.replace("Z", "+00:00"))
            except Exception:
                dt = None
        label = dt.strftime("%Y-%m-%d") if dt else (gen or "unknown")
        score = _overall_score(obj)
        winrt = _weighted_winrate(obj)
        self_score = obj.get("self_score")
        horizon = obj.get("horizon_days", 7)
        points.append(dict(label=label, score=score, winrt=winrt, self_score=self_score, horizon=horizon, raw=obj))
    # ベストインデックス
    best_idx = None
    best_val = None
    for i, p in enumerate(points):
        if p["score"] is None:
            continue
        if (best_val is None) or (p["score"] > best_val):
            best_val = p["score"]; best_idx = i
    return points, best_idx

# ========= View =========
@require_http_methods(["GET", "POST"])
def policy_history(request):
    """
    GET:   履歴のグラフ表示
    POST:  学習を実行（advisor_learn）→ policy.json を上書き、任意で advisor_snapshot 作成
    """
    flash: Optional[str] = None

    if request.method == "POST":
        # form 値
        days = int(request.POST.get("days") or "90")
        make_snap = bool(request.POST.get("make_snap"))
        notify = (request.POST.get("notify") or "").strip()

        # 1) （任意）スナップショット（policy_YYYY-MM-DD.json）作成
        if make_snap:
            try:
                # advisor_policy_snapshot (days はダッシュボードに記録のため渡す)
                call_command("advisor_policy_snapshot", days=days)
                messages.success(request, "policy のスナップショットを作成しました。")
            except Exception as e:
                messages.warning(request, f"スナップショット作成に失敗: {e}")

        # 2) 学習（advisor_learn）→ policy.json 反映
        try:
            call_command("advisor_learn", days=days, out="media/advisor/policy.json")
            flash = f"policy.json を更新しました（days={days}）。"
        except Exception as e:
            messages.error(request, f"学習に失敗しました: {e}")

        # 3) （任意）通知メール
        if notify:
            try:
                call_command("advisor_run", email=notify, dry_run=True)
                messages.success(request, f"テスト通知（dry-run）を {notify} に送信しました。")
            except Exception as e:
                messages.warning(request, f"通知送信に失敗: {e}")

        # Post/Redirect/Get で二重送信防止
        if flash:
            request.session["flash_msg"] = flash
        return redirect("policy_history")

    # GET: 履歴読み込み
    hist = _load_history()
    points, best_idx = _points_from_history(hist)
    ctx = dict(
        has_data=len(points) > 0,
        points=points,
        best_idx=best_idx,
        best=(points[best_idx] if best_idx is not None else None),
        now=timezone.now(),
        flash_msg=request.session.pop("flash_msg", None),
    )
    return render(request, "advisor_policy.html", ctx)