# aiapp/services/fundamentals/settings.py
# -*- coding: utf-8 -*-
"""
fundamentals の共通設定。

- input_fund.json（手入力・外部取り込みの受け皿）
- latest_fund.json（正規化＋スコア付与済みの運用ファイル）
"""

from __future__ import annotations

from pathlib import Path

FUND_DIR = Path("media/aiapp/fundamentals")
FUND_DIR.mkdir(parents=True, exist_ok=True)

FUND_INPUT = FUND_DIR / "input_fund.json"
FUND_LATEST = FUND_DIR / "latest_fund.json"