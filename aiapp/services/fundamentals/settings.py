# aiapp/services/fundamentals/settings.py
# -*- coding: utf-8 -*-
"""
財務ファンダメンタルのスナップショット出力先。

- まずはJSON（確実に動く）
- 後でDB化しても、この層のI/Fを維持すれば picks_build_hybrid は壊れない
"""

from __future__ import annotations

from pathlib import Path

FUND_DIR = Path("media/aiapp/fundamentals")
FUND_DIR.mkdir(parents=True, exist_ok=True)

FUND_LATEST = FUND_DIR / "latest_fund.json"