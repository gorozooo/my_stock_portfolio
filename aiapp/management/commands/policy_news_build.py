# aiapp/management/commands/policy_news_build.py
# -*- coding: utf-8 -*-
"""
これは何のファイル？
- policy_news_build コマンド（Hybrid用：ニュース/政策/社会情勢コンテキストをJSON化）

役割（初心者向け）:
- fundamentals_build が作った latest_fundamentals.json の meta.asof を見て、
  “その日付(asof)” に揃えた policy_news を作って保存する。
- いまは input_policy_news.json（手動seed）を元に作る段階。
  （無くても空で作って保存するので運用が止まらない）

出力:
- media/aiapp/policy_news/latest_policy_news.json
- media/aiapp/policy_news/{timestamp}_policy_news.json
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from django.core.management.base import BaseCommand

from aiapp.services.policy_news.build_service import build_policy_news_snapshot, emit_policy_news_json
from aiapp.services.policy_news.repo import load_policy_news_snapshot
from aiapp.services.policy_news.settings import POLICY_NEWS_DIR, LATEST_POLICY_NEWS

JST = timezone(timedelta(hours=9))

FUND_LATEST = Path("media/aiapp/fundamentals/latest_fundamentals.json")


def _safe_json_load(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_fund_asof_date() -> str:
    """
    fundamentals の meta.asof（ISO）から YYYY-MM-DD を作る。
    取れない場合は “今日(JST)” にフォールバック。
    """
    d = _safe_json_load(FUND_LATEST)
    iso = None
    try:
        iso = (d.get("meta") or {}).get("asof")
    except Exception:
        iso = None

    if isinstance(iso, str) and len(iso) >= 10:
        return iso[:10]
    return datetime.now(JST).strftime("%Y-%m-%d")


class Command(BaseCommand):
    help = "policy_news_build: ニュース/政策/社会情勢コンテキストJSONを生成（Hybrid用）"

    def handle(self, *args, **opts):
        asof = _extract_fund_asof_date()

        snap = build_policy_news_snapshot(asof=asof, source="manual_seed")
        emit_policy_news_json(snap)

        # 確認用に読み直して集計が入ってることを表示
        s2 = load_policy_news_snapshot()
        self.stdout.write(self.style.SUCCESS(
            f"policy_news_build: asof={s2.asof} items={len(s2.items)} wrote={str(LATEST_POLICY_NEWS)}"
        ))