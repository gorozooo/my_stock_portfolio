# aiapp/management/commands/policy_news_build.py
# -*- coding: utf-8 -*-
"""
policy_news_build コマンド（完全自動版）

- fundamentals の asof 日付に合わせて policy_news を生成
- latest_policy_news.json に出力
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from aiapp.services.policy_news.build_service import build_policy_news_snapshot, emit_policy_news_json
from aiapp.services.policy_news.settings import LATEST_POLICY_NEWS
from pathlib import Path
import json


FUND_LATEST = Path("media/aiapp/fundamentals/latest_fundamentals.json")


def _safe_json_load(path: Path):
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_fund_asof_date(fund):
    try:
        iso = (fund.get("meta") or {}).get("asof")
    except Exception:
        iso = None
    if isinstance(iso, str) and len(iso) >= 10:
        return iso[:10]
    return "1970-01-01"


class Command(BaseCommand):
    help = "policy_news_build: 市場データから policy_news JSON を完全自動生成"

    def handle(self, *args, **options):
        fund = _safe_json_load(FUND_LATEST)
        asof = _extract_fund_asof_date(fund)

        snap = build_policy_news_snapshot(asof=asof, source="auto_market")
        emit_policy_news_json(snap)

        self.stdout.write(
            f"policy_news_build: asof={snap.asof} items={len(snap.items)} wrote={str(LATEST_POLICY_NEWS)}"
        )