# aiapp/services/daytrade/policy_loader.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from django.conf import settings

from .policy_schema import PolicySchemaError, validate_policy_dict


class PolicyLoadError(RuntimeError):
    """Raised when the policy file cannot be loaded or validated."""


@dataclass(frozen=True)
class LoadedPolicy:
    policy: Dict[str, Any]
    path: Path

    @property
    def policy_id(self) -> str:
        return str(self.policy.get("meta", {}).get("policy_id", ""))


def _project_root() -> Path:
    """
    Resolve project root (where manage.py usually exists).
    We use BASE_DIR from Django settings as the anchor.
    """
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is None:
        raise PolicyLoadError("Django settings.BASE_DIR is not set.")
    return Path(base_dir).resolve()


def default_active_policy_path() -> Path:
    """
    Default: <project_root>/policies/daytrade/active.yml
    """
    return _project_root() / "policies" / "daytrade" / "active.yml"


def load_policy_yaml(path: Optional[Path] = None) -> LoadedPolicy:
    """
    Load and validate the active policy YAML.
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