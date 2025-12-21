# aiapp/services/policy_loader.py
from __future__ import annotations

import os
import shutil
from typing import Any, Dict

import yaml
from django.conf import settings


def _policy_base_dir() -> str:
    """
    aiapp/policies ディレクトリへのパスを返す。
    """
    return os.path.join(settings.BASE_DIR, "aiapp", "policies")


def _policy_template_path(name: str) -> str:
    """
    テンプレ（Git管理）: {name}.yml
    """
    base_dir = _policy_base_dir()
    return os.path.join(base_dir, f"{name}.yml")


def _policy_runtime_path(name: str) -> str:
    """
    runtime（Git管理外）: {name}.runtime.yml
    """
    base_dir = _policy_base_dir()
    return os.path.join(base_dir, f"{name}.runtime.yml")


def ensure_runtime_policy(name: str) -> str:
    """
    runtime が無ければテンプレから生成して返す。
    """
    runtime = _policy_runtime_path(name)
    tmpl = _policy_template_path(name)
    os.makedirs(os.path.dirname(runtime), exist_ok=True)

    if not os.path.exists(runtime):
        if os.path.exists(tmpl):
            shutil.copyfile(tmpl, runtime)
        else:
            # テンプレも無い場合は空で作る（落とさない）
            with open(runtime, "w", encoding="utf-8") as f:
                yaml.safe_dump({}, f, allow_unicode=True, sort_keys=False)

    return runtime


def load_policy(name: str = "short_aggressive") -> Dict[str, Any]:
    """
    ポリシーを dict で返す。
    優先順位:
      1) {name}.runtime.yml（運用 / 真実ソース）
      2) {name}.yml（テンプレ / 初期値）
    ※設定画面で書き換えるのは runtime 側。
    """
    # runtime を確実に用意
    runtime_path = ensure_runtime_policy(name)

    # runtime -> dict
    data: Dict[str, Any] = {}
    try:
        with open(runtime_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                data = loaded
            else:
                data = {}
    except Exception:
        data = {}

    # どうしても空ならテンプレを読む（保険）
    if not data:
        tmpl_path = _policy_template_path(name)
        if os.path.exists(tmpl_path):
            try:
                with open(tmpl_path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f) or {}
                    if isinstance(loaded, dict):
                        data = loaded
            except Exception:
                pass

    return data


def load_short_aggressive_policy() -> Dict[str, Any]:
    """
    短期×攻め 用ポリシーを読み込むショートカット。
    """
    return load_policy("short_aggressive")