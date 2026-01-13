# aiapp/services/policy_news/settings.py
# -*- coding: utf-8 -*-
"""
政策・社会情勢スナップショットの出力先。

- 入力（手動/別ジョブ）: media/aiapp/policy/input_policy.json
- 出力（picks_build_hybridが読む）: media/aiapp/policy/latest_policy.json
"""

from __future__ import annotations

from pathlib import Path

POLICY_DIR = Path("media/aiapp/policy")
POLICY_DIR.mkdir(parents=True, exist_ok=True)

POLICY_LATEST = POLICY_DIR / "latest_policy.json"
POLICY_INPUT = POLICY_DIR / "input_policy.json"