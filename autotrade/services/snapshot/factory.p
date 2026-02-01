"""
[FILE] autotrade/services/snapshot/factory.py
[PATH] <project_root>/autotrade/services/snapshot/factory.py

このファイルは何？
- 画面で調整した数値（TuningProfile.params）を
  「一生ズレない固定版（SettingSnapshot）」に変換して保存するためのファイルです。

なぜ必要？
- TuningProfile は “作業台” なので、後からいくらでも変わります。
- そのままバックテストや本番に使うと「結果の再現性」が壊れます。
- だから、必ず Snapshot（固定）を作ってから、それを入力にします。

初心者ポイント：
- 「下書き（作業台）」→「固定（スナップショット）」にしてからテストする
  という流れを守ると、あとで混乱しません。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from django.db import transaction
from django.utils import timezone

from autotrade.models import AutoTradeSettingSnapshot, AutoTradeTuningProfile
from autotrade.services.params.validator import fill_defaults, validate_params, ParamValidationError


def _ensure_jsonable(params: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    JSONField に安全に保存できる形に整形します。

    注意：
    - Pythonのfloat/intは基本OKですが、
      将来的にDecimal等が混ざっても事故らないように「素の型」に寄せます。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for cat, items in params.items():
        out[cat] = {}
        for k, v in items.items():
            # bool は int のサブクラスなので、ここでは想定しない（schemaに無い）
            if isinstance(v, (int, float, str)) or v is None:
                out[cat][k] = v
            else:
                # 変な型が混ざったら文字列化（ただし基本ここには来ない想定）
                out[cat][k] = str(v)
    return out


@transaction.atomic
def create_snapshot_from_profile(
    profile: AutoTradeTuningProfile,
    *,
    label: Optional[str] = None,
    status: str = "DRAFT",
) -> AutoTradeSettingSnapshot:
    """
    TuningProfile（作業台）から SettingSnapshot（固定版）を作成します。

    ルール：
    - ここで default 埋め（fill_defaults）
    - ここで安全チェック（validate_params）
    - ここで固定して DB に保存

    引数:
      profile: AutoTradeTuningProfile（編集用の設定）
      label: 画面表示用のラベル（省略時は日時で自動作成）
      status: "DRAFT" / "CANDIDATE" / "ACTIVE" / "RETIRED"

    戻り値:
      作成された AutoTradeSettingSnapshot
    """
    raw_params = profile.params or {}
    params = fill_defaults(raw_params)

    # 危険値や不足を入口で止める
    validate_params(params)

    # JSONとして安全な形に寄せる
    snapshot_json = _ensure_jsonable(params)

    if not label:
        # 例: "2026-02-01 22:10 snapshot"
        now = timezone.localtime(timezone.now())
        label = now.strftime("%Y-%m-%d %H:%M snapshot")

    snap = AutoTradeSettingSnapshot.objects.create(
        user=profile.user,
        source_profile=profile,
        label=label,
        status=status,
        snapshot=snapshot_json,
    )
    return snap


@transaction.atomic
def create_snapshot_from_params(
    *,
    user,
    params: Dict[str, Dict[str, Any]],
    label: str,
    status: str = "DRAFT",
    source_profile: Optional[AutoTradeTuningProfile] = None,
) -> AutoTradeSettingSnapshot:
    """
    UIやAPIから直接 params を受け取り、SettingSnapshot（固定版）を作成します。

    使いどころ：
    - UIで「テスト実行」を押したとき
      → その瞬間の数値を固定したい（＝Snapshot）

    注意：
    - params は “部分送信” されることがあるので、fill_defaults で必ず完全形にします。
    """
    filled = fill_defaults(params)
    validate_params(filled)
    snapshot_json = _ensure_jsonable(filled)

    snap = AutoTradeSettingSnapshot.objects.create(
        user=user,
        source_profile=source_profile,
        label=label,
        status=status,
        snapshot=snapshot_json,
    )
    return snap


def safe_validate_for_ui(params: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    UI用：例外を投げず、結果を辞書で返すバリデーション。

    返り値:
      {
        "ok": True/False,
        "errors": [ "日本語エラー1", "日本語エラー2", ... ],
        "filled": <default埋めした完全形 params>  # ok=Falseでも返す（UIで表示に使える）
      }

    初心者ポイント：
    - UI側はこれを呼んで、ok=Falseなら赤字でerrorsを出すだけにできる。
    """
    filled = fill_defaults(params or {})
    try:
        validate_params(filled)
        return {"ok": True, "errors": [], "filled": filled}
    except ParamValidationError as e:
        msg = str(e).strip()
        errors = [line.strip() for line in msg.split("\n") if line.strip()]
        return {"ok": False, "errors": errors, "filled": filled}