# aiapp/services/policy_loader.py
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict

import yaml
from django.conf import settings


def _policy_base_dir() -> str:
    """
    aiapp/policies ディレクトリへのパスを返す。
    """
    # settings.BASE_DIR / "aiapp" / "policies"
    return os.path.join(settings.BASE_DIR, "aiapp", "policies")


@lru_cache
def load_policy(name: str = "short_aggressive") -> Dict[str, Any]:
    """
    任意のポリシーファイル（.yml）を読み込んで dict を返す。
    例: name="short_aggressive" -> aiapp/policies/short_aggressive.yml
    """
    base_dir = _policy_base_dir()
    filename = f"{name}.yml"
    path = os.path.join(base_dir, filename)

    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        # 何かあってもアプリ全体を落とさない
        return {}

    # dict を期待する
    if not isinstance(data, dict):
        return {}

    return data


def load_short_aggressive_policy() -> Dict[str, Any]:
    """
    短期×攻め 用ポリシー（short_aggressive.yml）を読み込むショートカット。
    """
    return load_policy("short_aggressive")