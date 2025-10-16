# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, glob, shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from django.conf import settings
from django.http import (
    JsonResponse, HttpRequest, HttpResponseBadRequest, HttpResponse
)
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.core.management import call_command
from django.core.mail import send_mail

# ===================== 共通ヘルパ =====================

def _media_root() -> Path:
    """MEDIA_ROOT があればそこ、なければ cwd。"""
    return Path(getattr(settings, "MEDIA_ROOT", os.getcwd()))

def _policy_main_path() -> Path:
    """
    既定の policy.json の場所を返す。
    優先: MEDIA_ROOT/advisor/policy.json
    代替: MEDIA_ROOT/media/advisor/policy.json
    """
    base = _media_root()
    p1 = base / "advisor" / "policy.json"
    p2 = base / "media" / "advisor" / "policy.json"
    return p1 if p1.exists() or not p2.exists() else p2

def _policy_history_dir() -> Path:
    """
    policy 履歴ディレクトリの実体を返す。
    優先: MEDIA_ROOT/advisor/history/
    代替: MEDIA_ROOT/media/advisor/history/
    """
    base = _media_root()
    d1 = base / "advisor" / "history"
    d2 = base / "media" / "advisor" / "history"
    return d1 if d1.exists() or not d2.exists() else d2

def _rotate_policy_history(main_fp: Path) -> Optional[Path]:
    """main policy.json を policy_YYYY-MM-DD.json として history に複写。"""
    try:
        hist_dir = _policy_history_dir()
        hist_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d")
        dst = hist_dir / f"policy_{ts}.json"
        shutil.copyfile(main_fp, dst)
        return dst
    except Exception:
        return None

def _load_json(path: Path) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_json_atomic(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _reload_advisor_cache_safely() -> None:
    """
    services.advisor にポリシーキャッシュのリロード関数があれば呼ぶ。
    （無くても無害）
    """
    try:
        from ..services import advisor as svc_advisor
        if hasattr(svc_advisor, "reload_policy_cache"):
            svc_advisor.reload_policy_cache()
    except Exception:
        pass

def _safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

# ===================== 履歴ロード & 指標 =====================

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
    MEDIA_ROOT/advisor/history/policy_YYYY-MM-DD.json を時系列で読み込む。
    無ければ MEDIA_ROOT/advisor/policy.json を単点として返す。
    """
    base = _media_root()
    hist_dir1 = base / "advisor" / "history"
    hist_dir2 = base / "media" / "advisor" / "history"
    main1 = base / "advisor" / "policy.json"
    main2 = base / "media" / "advisor" / "policy.json"

    paths: List[str] = []
    for d in (hist_dir1, hist_dir2):
        if d.is_dir():
            paths.extend(glob.glob(str(d / "policy_*.json")))

    items: List[Dict] = []
    for p in sorted(paths):
        try:
            with open(p, "r", encoding="utf-8") as f:
                items.append(json.load(f))
        except Exception:
            continue

    if not items:
        for p in (main1, main2):
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        items = [json.load(f)]
                except Exception:
                    pass
                break
    return items

# ===================== View: policy履歴（グラフ＋ベスト週） =====================

def policy_history(request: HttpRequest) -> HttpResponse:
    """
    policy 履歴を時系列で可視化＋“一番良かった週”を表示。
    templates/advisor_policy.html を描画。
    """
    hist = _load_history()
    points = []
    for obj in hist:
        gen = obj.get("generated_at") or obj.get("updated_at")
        # ISO 文字列をラベル化
        label = "unknown"
        if isinstance(gen, str):
            try:
                dt = datetime.fromisoformat(gen.replace("Z", "+00:00"))
                label = dt.strftime("%Y-%m-%d")
            except Exception:
                label = gen
        score = _overall_score(obj)
        winrt = _weighted_winrate(obj)
        horizon = obj.get("horizon_days") or obj.get("days") or 7
        # 将来: self_score を使う場合はここで拾う
        self_score = obj.get("self_score")
        points.append(dict(label=label, score=score, winrt=winrt, horizon=horizon, self_score=self_score, raw=obj))

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

# ===================== API: 再訓練→適用→通知（統合ボタン） =====================

@require_POST
def policy_retrain_apply(request: HttpRequest) -> JsonResponse:
    """
    受け取り:
      - days: 90 / 365 / 730 など（必須）
      - email: 通知先（任意・カンマ区切り）
      - snapshot: "1" なら学習前に advisor_snapshot を実行（任意）
    処理:
      1) 任意で snapshot
      2) advisor_learn --days N --out <policy.json>
      3) policy.json を history にローテーション
      4) キャッシュ再読込
      5) 任意でメール通知
    """
    try:
        days = int(request.POST.get("days", "0"))
    except Exception:
        return HttpResponseBadRequest("days must be int")
    if days <= 0:
        return HttpResponseBadRequest("invalid days")

    email_raw = (request.POST.get("email") or "").strip()
    do_snapshot = (request.POST.get("snapshot") == "1")

    # 1) 任意 snapshot
    snap_ok = False
    if do_snapshot:
        try:
            call_command("advisor_snapshot")
            snap_ok = True
        except Exception:
            snap_ok = False  # 無ければ無視

    # 2) 学習（出力先は main policy.json を使う）
    out_main = _policy_main_path()
    base = _media_root()
    # call_command に渡す out は MEDIA_ROOT 相対でも絶対でもOK
    out_arg = str(out_main) if not str(out_main).startswith(str(base)) else str(out_main.relative_to(base))
    try:
        call_command("advisor_learn", days=days, out=out_arg)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"advisor_learn failed: {e}"}, status=500)

    # 3) 履歴ローテーション
    rotated_path = _rotate_policy_history(out_main)

    # 4) キャッシュ再読込
    _reload_advisor_cache_safely()

    # 5) 任意: メール通知
    mailed = False
    if email_raw:
        try:
            body = [
                f"[AI再訓練 完了] days={days}",
                f"snapshot: {'yes' if snap_ok else 'no'}",
                f"policy: {out_main}",
            ]
            if rotated_path:
                body.append(f"history: {rotated_path.name}")
            send_mail(
                subject=f"AI再訓練 完了 (days={days})",
                message="\n".join(body),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
                recipient_list=[x.strip() for x in email_raw.split(",") if x.strip()],
                fail_silently=True,
            )
            mailed = True
        except Exception:
            mailed = False

    return JsonResponse({
        "ok": True,
        "days": days,
        "snapshot": snap_ok,
        "policy_path": str(out_main),
        "history_saved": bool(rotated_path),
        "mailed": mailed,
    })