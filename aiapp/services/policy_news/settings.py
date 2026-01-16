# aiapp/services/policy_news/settings.py
# -*- coding: utf-8 -*-
"""
これは何のファイル？
- policy_news（ニュース/政策/社会情勢）JSONの入出力先やタイムスタンプ等の共通設定。

役割:
- media/aiapp/policy_news/ に
  - latest_policy_news.json
  - {timestamp}_policy_news.json
  を保存するためのパス・時刻設定を提供する。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

JST = dt_timezone(timedelta(hours=9))

POLICY_NEWS_DIR = Path("media/aiapp/policy_news")
POLICY_NEWS_DIR.mkdir(parents=True, exist_ok=True)

LATEST_POLICY_NEWS = POLICY_NEWS_DIR / "latest_policy_news.json"


def dt_now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")