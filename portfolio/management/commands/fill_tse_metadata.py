# portfolio/management/commands/fill_tse_metadata.py
from __future__ import annotations
import json, os
from datetime import date
from typing import Optional, Dict
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                         "data", "tse_list.json")

def load_map() -> Dict[str, Dict[str,str]]:
    if not os.path.exists(JSON_PATH):
        raise CommandError(f"tse_list.json が見つかりません: {JSON_PATH}（先に update_tse_list を実行）")
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def normalize_code(ticker: str) -> str:
    t = str(ticker).upper().strip()
    if t.endswith(".T"):
        t = t[:-2]
    return t

class Command(BaseCommand):
    help = "tse_list.json を使って TrendResult / Holding の name, sector, market を日本語で補完"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="全レコード対象（指定がなければ今日の TrendResult のみ）")
        parser.add_argument("--only-trend", action="store_true", help="TrendResultのみ更新")
        parser.add_argument("--only-holdings", action="store_true", help="Holdingのみ更新")

    def handle(self, *args, **opts):
        mapping = load_map()
        only_trend = bool(opts.get("only_trend"))
        only_hold  = bool(opts.get("only_holdings"))
        all_flag   = bool(opts.get("all"))

        # 優先ユーザー（存在しない場合も考慮）
        User = get_user_model()
        user = User.objects.first()

        total_updated = 0

        # ---- TrendResult ----
        if not only_hold:
            try:
                from advisor.models_trend import TrendResult
                qs = TrendResult.objects.all()
                if not all_flag:
                    qs = qs.filter(asof=date.today())
                if user:
                    qs = qs.filter(user=user)
                upd = 0
                for tr in qs:
                    code = normalize_code(tr.ticker)
                    info = mapping.get(code)
                    if not info:
                        continue
                    name = info.get("name") or tr.name
                    sector = info.get("sector") or getattr(tr, "sector", None)
                    market = info.get("market") or getattr(tr, "market", None)

                    changed = False
                    if name and tr.name != name:
                        tr.name = name; changed = True
                    # オプションフィールド（あれば更新）
                    if hasattr(tr, "sector") and sector and getattr(tr, "sector") != sector:
                        setattr(tr, "sector", sector); changed = True
                    if hasattr(tr, "market") and market and getattr(tr, "market") != market:
                        setattr(tr, "market", market); changed = True

                    if changed:
                        fields = ["name"]
                        if hasattr(tr, "sector"): fields.append("sector")
                        if hasattr(tr, "market"): fields.append("market")
                        tr.save(update_fields=fields)
                        upd += 1
                self.stdout.write(self.style.SUCCESS(f"TrendResult updated: {upd}"))
                total_updated += upd
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"TrendResult 更新スキップ: {e}"))

        # ---- Holding ----
        if not only_trend:
            try:
                from portfolio.models import Holding
                qs = Holding.objects.all()
                if user and hasattr(Holding, "user"):
                    qs = qs.filter(user=user)
                upd = 0
                for h in qs:
                    code = normalize_code(getattr(h, "ticker", "") or getattr(h, "code", ""))
                    if not code:
                        continue
                    info = mapping.get(code)
                    if not info:
                        continue
                    name = info.get("name") or getattr(h, "name", None)
                    sector = info.get("sector") or getattr(h, "sector", None)
                    market = info.get("market") or getattr(h, "market", None)

                    changed = False
                    if hasattr(h, "name") and name and h.name != name:
                        h.name = name; changed = True
                    if hasattr(h, "sector") and sector and h.sector != sector:
                        h.sector = sector; changed = True
                    if hasattr(h, "market") and market and h.market != market:
                        h.market = market; changed = True

                    if changed:
                        fields = []
                        if hasattr(h, "name"): fields.append("name")
                        if hasattr(h, "sector"): fields.append("sector")
                        if hasattr(h, "market"): fields.append("market")
                        h.save(update_fields=fields)
                        upd += 1
                self.stdout.write(self.style.SUCCESS(f"Holding updated: {upd}"))
                total_updated += upd
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Holding 更新スキップ: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Total updated: {total_updated}"))