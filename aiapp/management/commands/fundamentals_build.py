# aiapp/management/commands/fundamentals_build.py
# -*- coding: utf-8 -*-
"""
財務ファンダスナップショット生成コマンド。

目的:
- media/aiapp/fundamentals/input_fund.json を読み込み
- 各銘柄の metrics から fund_score(0..100) + flags を計算して
  media/aiapp/fundamentals/latest_fund.json に保存（timestampファイルも任意）

想定入力フォーマット例（最小）:
{
  "asof":"2026-01-13",
  "meta":{"note":"manual seed"},
  "rows":{
    "7203":{"metrics":{"roe":12.5,"op_margin":9.2,"sales_yoy":5.1,"equity_ratio":35.0,"per":11.8}},
    "4502":{"metrics":{"roe":6.0,"op_margin":18.0,"sales_yoy":2.0,"equity_ratio":55.0,"per":22.0}}
  }
}

注意:
- 取得元（決算API/EDINET/スクレイピング等）は未確定でも、この方式なら確実に運用できる
- metrics の項目は増やしてOK（scoring.py が見るものだけ効く）
"""

from __future__ import annotations

import json
from datetime import date

from django.core.management.base import BaseCommand

from aiapp.services.picks_build.settings import dt_now_stamp
from aiapp.services.picks_build.utils import normalize_code
from aiapp.services.fundamentals.settings import FUND_INPUT, FUND_DIR
from aiapp.services.fundamentals.schema import FundamentalRow, FundamentalSnapshot
from aiapp.services.fundamentals.repo import save_fund_snapshot
from aiapp.services.fundamentals.scoring import score_fundamentals


class Command(BaseCommand):
    help = "財務ファンダスナップショット生成（input_fund.json → latest_fund.json）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            type=str,
            default=None,
            help="入力JSONファイル名（省略で media/aiapp/fundamentals/input_fund.json）",
        )
        parser.add_argument(
            "--stamp",
            action="store_true",
            help="timestamp付きファイルも保存する",
        )

    def handle(self, *args, **opts):
        input_name = opts.get("input")
        do_stamp = bool(opts.get("stamp"))

        p = FUND_INPUT if not input_name else (FUND_DIR / input_name)
        if not p.exists():
            self.stdout.write(self.style.WARNING(f"[fundamentals_build] input not found: {p}"))
            snap = FundamentalSnapshot(asof=date.today().isoformat(), rows={}, meta={"source": "missing_input"})
            stamp_name = f"{dt_now_stamp()}_fund.json" if do_stamp else None
            save_fund_snapshot(snap, stamp_name=stamp_name)
            self.stdout.write(self.style.SUCCESS("[fundamentals_build] wrote empty latest_fund.json"))
            return

        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception as ex:
            self.stdout.write(self.style.ERROR(f"[fundamentals_build] json parse error: {ex}"))
            snap = FundamentalSnapshot(asof=date.today().isoformat(), rows={}, meta={"source": "json_error"})
            stamp_name = f"{dt_now_stamp()}_fund.json" if do_stamp else None
            save_fund_snapshot(snap, stamp_name=stamp_name)
            return

        asof = str(j.get("asof") or date.today().isoformat())
        meta = dict(j.get("meta") or {})
        meta.setdefault("source", "input_fund")
        meta["input_file"] = p.name

        rows_in = dict(j.get("rows") or {})
        rows = {}

        for code_raw, v in rows_in.items():
            code = normalize_code(code_raw)
            if not code:
                continue
            vv = dict(v or {})
            metrics = dict(vv.get("metrics") or {})
            fund_score, flags = score_fundamentals(metrics)

            rows[code] = FundamentalRow(
                code=code,
                asof=asof,
                fund_score=float(fund_score),
                flags=list(flags or [])[:10],
                metrics=metrics,
            )

        snap = FundamentalSnapshot(asof=asof, rows=rows, meta=meta)

        stamp_name = f"{dt_now_stamp()}_fund.json" if do_stamp else None
        save_fund_snapshot(snap, stamp_name=stamp_name)

        self.stdout.write(self.style.SUCCESS(f"[fundamentals_build] ok asof={asof} rows={len(rows)} file={p.name}"))