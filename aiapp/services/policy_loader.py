# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_POLICY_PATH = Path("aiapp/policies/scoring.yaml")

class PolicyLoader:
    """
    YAMLポリシーの読み込み。profiles(dev/prod)・regime・modeで該当ルールを引く。
    """
    def __init__(self, path: Optional[Path] = None):
        self.path = path or DEFAULT_POLICY_PATH
        self.data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Policy file not found: {self.path}")
        self.data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    def get_profile(self) -> str:
        return os.getenv("AIAPP_CONF_PROFILE", self.data.get("default_profile", "dev"))

    def weights(self, regime: str, mode: str, profile: Optional[str] = None) -> Dict[str, float]:
        profile = profile or self.get_profile()
        return (
            self.data.get("profiles", {})
                .get(profile, {})
                .get("weights", {})
                .get(regime, {})
                .get(mode, {})
            or {}
        )

    def entry_rule(self, mode: str, profile: Optional[str] = None) -> Dict[str, float]:
        profile = profile or self.get_profile()
        return (
            self.data.get("profiles", {})
                .get(profile, {})
                .get("entry_rules", {})
                .get(mode, {})
            or {}
        )

    def confidence_rule(self, profile: Optional[str] = None) -> Dict[str, Any]:
        profile = profile or self.get_profile()
        return (
            self.data.get("profiles", {})
                .get(profile, {})
                .get("confidence", {})
            or {}
        )