# aiapp/management/commands/policy_build.py
# -*- coding: utf-8 -*-
"""
政策・社会情勢スナップショット生成コマンド。

目的:
- media/aiapp/policy/input_policy.json を読み込み
- media/aiapp/policy/latest_policy.json に正規化して保存（timestampファイルも任意で保存）

想定入力フォーマット例（最小）:
{
  "asof":"2026-01-13",
  "meta":{"note":"manual seed"},
  "sector_rows":{
    "輸送用機器":{"policy_score":5,"flags":["円安追い風(仮)"],"meta":{"source":"manual"}},
    "医薬品":{"policy_score":-8,"flags":["薬価改定リスク(仮)"],"meta":{"source":"manual"}}
  }
}

注意:
- 取得元（ニュース/政策API）は未確定でも、このコマンドで “必ず動く” 形に固定する
"""

from __future__ import annotations

import json
from datetime import date

from django.core.management.base import BaseCommand

from aiapp.services.picks_build.settings import dt_now_stamp
from aiapp.services.policy_news.settings import POLICY_INPUT, POLICY_DIR
from aiapp.services.policy_news.schema import PolicySectorRow, PolicySnapshot
from aiapp.services.policy_news.repo import save_policy_snapshot


class Command(BaseCommand):
    help = "政策・社会情勢スナップショット生成（input_policy.json → latest_policy.json）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            type=str,
            default=None,
            help="入力JSONファイル名（省略で media/aiapp/policy/input_policy.json）",
        )
        parser.add_argument(
            "--stamp",
            action="store_true",
            help="timestamp付きファイルも保存する",
        )

    def handle(self, *args, **opts):
        input_name = opts.get("input")
        do_stamp = bool(opts.get("stamp"))

        p = POLICY_INPUT if not input_name else (POLICY_DIR / input_name)
        if not p.exists():
            self.stdout.write(self.style.WARNING(f"[policy_build] input not found: {p}"))
            # 空でも latest を作る（hybridが必ず動くため）
            snap = PolicySnapshot(asof=date.today().isoformat(), sector_rows={}, meta={"source": "missing_input"})
            stamp_name = f"{dt_now_stamp()}_policy.json" if do_stamp else None
            save_policy_snapshot(snap, stamp_name=stamp_name)
            self.stdout.write(self.style.SUCCESS("[policy_build] wrote empty latest_policy.json"))
            return

        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception as ex:
            self.stdout.write(self.style.ERROR(f"[policy_build] json parse error: {ex}"))
            snap = PolicySnapshot(asof=date.today().isoformat(), sector_rows={}, meta={"source": "json_error"})
            stamp_name = f"{dt_now_stamp()}_policy.json" if do_stamp else None
            save_policy_snapshot(snap, stamp_name=stamp_name)
            return

        asof = str(j.get("asof") or date.today().isoformat())
        meta = dict(j.get("meta") or {})
        meta.setdefault("source", "input_policy")
        meta["input_file"] = p.name

        rows_in = dict(j.get("sector_rows") or {})
        sector_rows = {}

        for sec, v in rows_in.items():
            sector = str(sec or "").strip()
            if not sector:
                continue
            vv = dict(v or {})
            policy_score = float(vv.get("policy_score") or 0.0)
            flags = list(vv.get("flags") or [])[:10]
            m = dict(vv.get("meta") or {})
            sector_rows[sector] = PolicySectorRow(
                sector_display=sector,
                policy_score=policy_score,
                flags=flags,
                meta=m,
            )

        snap = PolicySnapshot(asof=asof, sector_rows=sector_rows, meta=meta)

        stamp_name = f"{dt_now_stamp()}_policy.json" if do_stamp else None
        save_policy_snapshot(snap, stamp_name=stamp_name)

        self.stdout.write(self.style.SUCCESS(f"[policy_build] ok asof={asof} sectors={len(sector_rows)} file={p.name}"))