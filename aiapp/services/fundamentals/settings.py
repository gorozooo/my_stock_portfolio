# aiapp/services/fundamentals/settings.py
# -*- coding: utf-8 -*-
"""
財務ファンダメンタルのスナップショット出力先。

- 入力（手動/別ジョブ）: media/aiapp/fundamentals/input_fund.json
- 出力（picks_build_hybridが読む）: media/aiapp/fundamentals/latest_fund.json
"""

from __future__ import annotations

from pathlib import Path

FUND_DIR = Path("media/aiapp/fundamentals")
FUND_DIR.mkdir(parents=True, exist_ok=True)

FUND_LATEST = FUND_DIR / "latest_fund.json"
FUND_INPUT = FUND_DIR / "input_fund.json"