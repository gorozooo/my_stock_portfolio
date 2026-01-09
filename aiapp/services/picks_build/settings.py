# -*- coding: utf-8 -*-
"""
picks_build の共通設定。

- JST timezone
- 出力先ディレクトリ
- 環境変数フラグ（BUILD_LOG / CONF_DETAIL）
- timestamp 生成
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

PICKS_DIR = Path("media/aiapp/picks")
PICKS_DIR.mkdir(parents=True, exist_ok=True)

JST = dt_timezone(timedelta(hours=9))


def dt_now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


BUILD_LOG = _env_bool("AIAPP_BUILD_LOG", False)
CONF_DETAIL = _env_bool("AIAPP_CONF_DETAIL", False)  # 1なら confidence_detail を meta に入れる（重いので通常OFF）