# aiapp/services/policy_news/settings.py
# -*- coding: utf-8 -*-
"""
policy_news の共通設定。

- input_policy.json（手入力・外部取り込みの受け皿）
- latest_policy.json（正規化済みの運用ファイル）
"""

from __future__ import annotations

from pathlib import Path

POLICY_DIR = Path("media/aiapp/policy")
POLICY_DIR.mkdir(parents=True, exist_ok=True)

POLICY_INPUT = POLICY_DIR / "input_policy.json"
POLICY_LATEST = POLICY_DIR / "latest_policy.json"