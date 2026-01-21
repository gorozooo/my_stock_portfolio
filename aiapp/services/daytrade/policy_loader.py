# -*- coding: utf-8 -*-
"""
ファイル: aiapp/services/daytrade/policy_loader.py

これは何？
- policies/daytrade/active.yml を読み込んで、Pythonの dict に変換し、
  policy_schema.py の検証（バリデーション）を通してから返す「読み込み係」です。
- 以後、バックテストも本番全自動も、まずこのローダで active.yml を読み込みます。

置き場所（重要）
- プロジェクトルート（manage.py がある階層）から見て:
  aiapp/services/daytrade/policy_loader.py

読み込むYAMLの場所（重要）
- active.yml（本番が読む“現行憲法”）:
  <project_root>/policies/daytrade/active.yml
  ※ <project_root> は Django の settings.BASE_DIR を基準にします。

運用上の注意（超重要）
- active.yml や snapshots/ は、バックテストやFixerで書き換わる運用ファイルです。
  Git管理する場合は .gitignore で除外するのが安全です。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from django.conf import settings

from .policy_schema import PolicySchemaError, validate_policy_dict


class PolicyLoadError(RuntimeError):
    """ポリシーYAMLを読み込めない／検証に失敗した場合に投げる例外です。"""


@dataclass(frozen=True)
class LoadedPolicy:
    """
    読み込み済みのポリシー（active.yml）の入れ物。

    - policy: YAMLの内容（dict）
    - path: 読み込んだファイルパス（active.yml）
    """
    policy: Dict[str, Any]
    path: Path

    @property
    def policy_id(self) -> str:
        return str(self.policy.get("meta", {}).get("policy_id", ""))


def _project_root() -> Path:
    """
    プロジェクトルートを解決する（通常、manage.py がある階層）。

    Djangoの settings.BASE_DIR を使って決める。
    """
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        raise PolicyLoadError("Django settings.BASE_DIR is not set.")
    return Path(base_dir).resolve()


def default_active_policy_path() -> Path:
    """
    active.yml のデフォルトパス:
      <project_root>/policies/daytrade/active.yml
    """
    return _project_root() / "policies" / "daytrade" / "active.yml"


def load_policy_yaml(path: Optional[Path] = None) -> LoadedPolicy:
    """
    active.yml を読み込んで検証した上で返す。

    使い方（例）:
      from aiapp.services.daytrade.policy_loader import load_policy_yaml
      p = load_policy_yaml()
      print(p.policy_id)
    """
    p = (path or default_active_policy_path()).resolve()

    if not p.exists():
        raise PolicyLoadError(
            f"Active policy file not found: {p}\n"
            f"Expected location: <project_root>/policies/daytrade/active.yml"
        )

    if not p.is_file():
        raise PolicyLoadError(f"Active policy path is not a file: {p}")

    try:
        raw = p.read_text(encoding="utf-8")
    except Exception as e:
        raise PolicyLoadError(f"Failed to read policy file: {p} ({e})") from e

    try:
        data = yaml.safe_load(raw)
    except Exception as e:
        raise PolicyLoadError(f"Failed to parse YAML: {p} ({e})") from e

    if not isinstance(data, dict):
        raise PolicyLoadError(f"Policy YAML root must be a mapping (dict): {p}")

    try:
        validate_policy_dict(data)
    except PolicySchemaError as e:
        raise PolicyLoadError(f"Policy schema validation failed: {p}\n{e}") from e

    return LoadedPolicy(policy=data, path=p)