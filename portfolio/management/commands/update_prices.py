# portfolio/management/commands/update_prices.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import csv
import os
from datetime import datetime, timezone as _tz
from typing import Optional, Dict

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.utils import timezone

from ...models import Holding


def _parse_float(x: str) -> Optional[float]:
    try:
        if x is None:
            return None
        s = str(x).strip().replace(",", "")
        return float(s) if s != "" else None
    except Exception:
        return None


def _load_csv_prices(path: str) -> Dict[str, float]:
    """
    CSV 形式:
        ticker,price,date
        7203,3700,2025-10-15
        8306,1241.5,2025-10-15
    ※ ヘッダ必須。date は任意（なければ today）。
    """
    out: Dict[str, float] = {}
    with open(path, newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            t = (row.get("ticker") or "").strip().upper()
            p = _parse_float(row.get("price"))
            if not t or p is None:
                continue
            out[t] = p
    return out


class Command(BaseCommand):
    help = "Holdings の last_price / last_price_updated を更新します。（CSV 取り込み対応）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--csv",
            type=str,
            help="終値CSVのパス（ticker,price[,date] ヘッダ必須）",
        )
        parser.add_argument(
            "--stale-days",
            type=int,
            default=None,
            help="価格が古い銘柄だけを更新対象にする閾値（日数）。未指定なら全件対象。",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="DBを書き換えずに変更予定だけ表示",
        )
        parser.add_argument(
            "--print",
            action="store_true",
            help="更新サマリを標準出力へ表示",
        )

    def handle(self, *args, **opts):
        csv_path = opts.get("csv")
        stale_days = opts.get("stale_days")
        dry = bool(opts.get("dry_run"))
        do_print = bool(opts.get("print"))

        if not csv_path or not os.path.exists(csv_path):
            self.stderr.write(self.style.ERROR("CSV パスが無効です。--csv /path/to/prices.csv を指定してください。"))
            return

        prices = _load_csv_prices(csv_path)
        if not prices:
            self.stderr.write(self.style.WARNING("CSV に有効な行がありませんでした。"))
            return

        now = timezone.now()
        updated = 0
        skipped = 0

        # 対象の抽出
        qs = Holding.objects.all()
        if stale_days is not None and stale_days >= 0:
            cutoff = now - timezone.timedelta(days=stale_days)
            qs = qs.filter(last_price_updated__lt=cutoff) | qs.filter(last_price_updated__isnull=True)

        with transaction.atomic():
            for h in qs:
                t = (h.ticker or "").strip().upper()
                if t not in prices:
                    skipped += 1
                    continue
                new_p = prices[t]
                # 変化がなければスキップ
                if h.last_price is not None and float(h.last_price) == float(new_p):
                    skipped += 1
                    continue

                if not dry:
                    h.last_price = new_p
                    h.last_price_updated = now
                    h.save(update_fields=["last_price", "last_price_updated"])
                updated += 1

            if dry:
                transaction.set_rollback(True)

        if do_print:
            self.stdout.write(f"[update_prices] file={csv_path} targets={qs.count()} updated={updated} skipped={skipped} dry={dry}")

        self.stdout.write(self.style.SUCCESS(f"[update_prices] updated={updated}, skipped={skipped}"))