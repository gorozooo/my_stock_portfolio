# aiapp/management/commands/policy_build.py
# -*- coding: utf-8 -*-
"""
policy_build（Hybrid用：ファンダ/政策コンテキストから “セクター方針スコア” をJSON化）

このコマンドの役割（初心者向けに超ざっくり）:
- fundamentals_build が作った「市場コンテキストJSON（指数/先物など）」を参照し、
  policy（方針）JSON を “その日のもの” として確定して保存する。
- いまは “仮の手動seed” を input_policy.json に置いて動かす段階だが、
  asof（日付）だけは毎回 最新fundamentals に揃えないと A/B運用でズレる。

出力:
- media/aiapp/policy/latest_policy.json
- media/aiapp/policy/{timestamp}_policy.json

入力（仮seed）:
- media/aiapp/policy/input_policy.json
  ※ sector_rows（スコア/flags/why）はこれをベースにする
  ※ asof は fundamentals の日付で上書きする（重要）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from django.core.management.base import BaseCommand

JST = timezone(timedelta(hours=9))

POLICY_DIR = Path("media/aiapp/policy")
FUND_DIR = Path("media/aiapp/fundamentals")

INPUT_POLICY = POLICY_DIR / "input_policy.json"
LATEST_POLICY = POLICY_DIR / "latest_policy.json"
LATEST_FUND = FUND_DIR / "latest_fundamentals.json"


def _dt_now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


def _safe_json_load(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_fund_asof_date() -> str:
    """
    fundamentals の meta.asof（ISO: 2026-01-15T...）から YYYY-MM-DD を作る。
    取れない場合は “今日(JST)” を返す。
    """
    d = _safe_json_load(LATEST_FUND)
    iso = None
    try:
        iso = (d.get("meta") or {}).get("asof")
    except Exception:
        iso = None

    if isinstance(iso, str) and len(iso) >= 10:
        # ISO の先頭10文字は YYYY-MM-DD
        return iso[:10]

    # フォールバック（JST今日）
    return datetime.now(JST).strftime("%Y-%m-%d")


@dataclass
class PolicySnapshot:
    asof: str
    sector_rows: Dict[str, Any]
    meta: Dict[str, Any]


def _build_policy_snapshot() -> PolicySnapshot:
    """
    いまは “仮seed” 運用:
    - input_policy.json をベースにする
    - ただし asof は fundamentals の日付に必ず揃える
    """
    POLICY_DIR.mkdir(parents=True, exist_ok=True)

    seed = _safe_json_load(INPUT_POLICY)

    # ベース（無ければ空）
    sector_rows = seed.get("sector_rows") if isinstance(seed.get("sector_rows"), dict) else {}
    meta = seed.get("meta") if isinstance(seed.get("meta"), dict) else {}

    # ★重要：asof は毎回 fundamentals 由来で上書き
    asof = _extract_fund_asof_date()

    # meta に “asofの根拠” を残しておく（デバッグが楽）
    meta2 = dict(meta)
    meta2["asof_source"] = "fundamentals/latest_fundamentals.json"
    meta2["fundamentals_asof_date"] = asof

    return PolicySnapshot(
        asof=asof,
        sector_rows=sector_rows,
        meta=meta2,
    )


def _emit_policy_json(snap: PolicySnapshot) -> None:
    POLICY_DIR.mkdir(parents=True, exist_ok=True)

    payload: Dict[str, Any] = {
        "asof": snap.asof,
        "sector_rows": snap.sector_rows,
        "meta": snap.meta,
    }

    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # latest
    LATEST_POLICY.write_text(s, encoding="utf-8")

    # stamped
    stamped = POLICY_DIR / f"{_dt_now_stamp()}_policy.json"
    stamped.write_text(s, encoding="utf-8")


class Command(BaseCommand):
    help = "policy_build: セクター方針スコアJSONを生成（Hybrid用）"

    def handle(self, *args, **opts):
        snap = _build_policy_snapshot()
        _emit_policy_json(snap)

        # ログは “policy asof” を出す（fundamentals由来の日付）
        self.stdout.write(self.style.SUCCESS(
            f"policy_build: asof={snap.asof} sectors={len(snap.sector_rows)}"
        ))