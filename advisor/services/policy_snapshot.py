# advisor/services/policy_snapshot.py
from __future__ import annotations
import os, json, datetime as dt
from typing import Dict, Any, Optional, List

from django.conf import settings
from django.utils.timezone import now as dj_now
from django.db import transaction

from advisor.models_policy import AdvisorPolicy, PolicySnapshot, DeviationLog

JST = dt.timezone(dt.timedelta(hours=9))

def _today_ymd(now: Optional[dt.datetime] = None) -> str:
    z = (now or dj_now()).astimezone(JST)
    return z.strftime("%Y%m%d")

def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def _media_base() -> str:
    # MEDIA_ROOT が未設定ならプロジェクト直下に media を作る
    base = getattr(settings, "MEDIA_ROOT", None)
    if not base:
        base = os.path.join(getattr(settings, "BASE_DIR", os.getcwd()), "media")
    return base

def snapshot_all_active_policies(save_files: bool = True) -> List[PolicySnapshot]:
    """
    アクティブな AdvisorPolicy を一括スナップショット。
    - DB(PolicySnapshot)へ保存
    - save_files=True の場合は media/advisor/policies/YYYYMMDD/<slug>.json へも保存
    戻り値: 作成/更新された PolicySnapshot のリスト
    """
    now = dj_now().astimezone(JST)
    ymd = _today_ymd(now)
    out: List[PolicySnapshot] = []

    # 保存先ディレクトリ
    media_dir = os.path.join(_media_base(), "advisor", "policies", ymd)
    if save_files:
        _ensure_dir(media_dir)

    # ---- ここがポイント：active ではなく is_active を優先。なければ active を fallback ----
    field_names = {f.name for f in AdvisorPolicy._meta.get_fields()}
    qs_all = AdvisorPolicy.objects.all()
    if "is_active" in field_names:
        qs = qs_all.filter(is_active=True)
    elif "active" in field_names:
        qs = qs_all.filter(active=True)
    else:
        # フィールドが無ければ全件を対象（将来のスキーマ変更に耐える）
        qs = qs_all

    qs = qs.order_by("-priority", "id")

    with transaction.atomic():
        for pol in qs:
            payload: Dict[str, Any] = {
                "meta": {
                    "name": pol.name,
                    "description": pol.description,
                    "priority": pol.priority,
                    # どちらの型でも値が分かるようにメタにも残す
                    "is_active": getattr(pol, "is_active", None),
                    "active": getattr(pol, "active", None),
                    "created_at": pol.created_at.isoformat() if pol.created_at else None,
                    "updated_at": pol.updated_at.isoformat() if pol.updated_at else None,
                    "snapshot_at": now.isoformat(),
                    "version": "v1",
                },
                "rules": pol.rule_json or {},
            }

            snap, _created = PolicySnapshot.objects.update_or_create(
                policy=pol,
                as_of=now.date(),
                defaults={"payload": payload},
            )
            out.append(snap)

            if save_files:
                slug = "".join(ch if ch.isalnum() else "-" for ch in pol.name).strip("-").lower() or f"policy-{pol.id}"
                fpath = os.path.join(media_dir, f"{slug}.json")
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)

    return out


def log_deviation(
    *,
    policy: AdvisorPolicy,
    ticker: str,
    action: str,
    reason: str,
    extra: Optional[Dict[str, Any]] = None,
) -> DeviationLog:
    """
    ルールからの逸脱を1行で記録するユーティリティ。
    - action: 'manual_entry' / 'manual_exit' / 'override_tp' など自由
    - reason: 必須（なぜ外れたのか）
    - extra: 任意メタ（スクショのパス、当時の気分等）
    """
    return DeviationLog.objects.create(
        policy=policy,
        ticker=(ticker or "").strip().upper(),
        action=action,
        reason=reason,
        meta=extra or {},
    )