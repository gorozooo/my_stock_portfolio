# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, shutil
from datetime import datetime
from pathlib import Path
from typing import Dict

from django.conf import settings
from django.http import JsonResponse, HttpRequest, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.core.management import call_command
from django.core.mail import send_mail

# 既存: policy履歴可視化ビューがあればそのまま残してOK
# from .policy_history import policy_history  ←別ファイルならそちらを使ってもOK

# ------- 共通 -------
def _media_root() -> Path:
    return Path(getattr(settings, "MEDIA_ROOT", os.getcwd()))

def _policy_main_path() -> Path:
    # media/advisor/policy.json（既定）
    base = _media_root()
    p1 = base / "advisor" / "policy.json"
    p2 = base / "media" / "advisor" / "policy.json"
    return p1 if p1.exists() or not p2.exists() else p2

def _policy_history_dir() -> Path:
    # media/advisor/history/
    base = _media_root()
    d1 = base / "advisor" / "history"
    d2 = base / "media" / "advisor" / "history"
    return d1 if d1.exists() or not d2.exists() else d2

def _rotate_policy_history(main_fp: Path) -> Path:
    hist_dir = _policy_history_dir()
    hist_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d")
    dst = hist_dir / f"policy_{ts}.json"
    try:
        shutil.copyfile(main_fp, dst)
    except Exception:
        pass
    return dst

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
    # services.advisor にキャッシュクリアAPIがあれば呼ぶ。なくても無害。
    try:
        from ..services import advisor as svc_advisor
        if hasattr(svc_advisor, "reload_policy_cache"):
            svc_advisor.reload_policy_cache()
    except Exception:
        pass

# ------- 統合トリガー（学習→適用→通知） -------
@require_POST
def policy_retrain_apply(request: HttpRequest):
    """
    受け取り:
      - days: 90 / 365 / 730 など（必須）
      - email: 通知先（任意・カンマ区切り）
      - snapshot: "1" なら学習前に advisor_snapshot を実行（任意）
    処理:
      1) 任意で snapshot（表示用）
      2) advisor_learn --days N → policy.json 出力
      3) policy.json を history にローテーション
      4) サービス側キャッシュ再読込
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

    # 1) 任意: スナップショット
    snap_ok = False
    if do_snapshot:
        try:
            # 以前作成したスナップショットコマンド名に合わせる
            # 例: advisor_snapshot（存在しない場合はスキップ）
            call_command("advisor_snapshot")
            snap_ok = True
        except Exception:
            snap_ok = False  # あってもなくても良い

    # 2) 学習（policy.json 出力先は既定の media/advisor/policy.json を想定）
    out_main = _policy_main_path()
    out_arg = str(out_main.relative_to(_media_root())) if str(out_main).startswith(str(_media_root())) else str(out_main)

    try:
        # あなたの環境の advisor_learn 引数に合わせています（--days / --out / --bias など）
        call_command("advisor_learn", days=days, out=out_arg)
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"advisor_learn failed: {e}"}, status=500)

    # 3) 履歴ローテーション（保存後に複写）
    rotated_path = None
    try:
        if out_main.exists():
            rotated_path = _rotate_policy_history(out_main)
    except Exception:
        pass

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

    # 返却（フロントはトースト表示後にリロード）
    payload = {
        "ok": True,
        "days": days,
        "snapshot": snap_ok,
        "policy_path": str(out_main),
        "history_saved": bool(rotated_path),
        "mailed": mailed,
    }
    return JsonResponse(payload)