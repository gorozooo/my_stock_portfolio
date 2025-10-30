# advisor/management/commands/policy_eval_exits.py
from __future__ import annotations
import csv
from typing import Optional, List, Dict, Any
from datetime import date, timezone, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from advisor.models_policy import AdvisorPolicy
from advisor.models_trend import TrendResult
from advisor.services.policy_rules import compute_exit_targets

JST = timezone(timedelta(hours=9))

def _load_csv(path: str) -> List[str]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row: continue
            t = str(row[0]).strip().upper()
            if t and not t.startswith("#"):
                out.append(t)
    return out

class Command(BaseCommand):
    help = "ポリシーの数値ルール（ATR/R/時間）で TP/SL/時間切れを試算して表示する（保存しない）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="today", help="today(直近TrendResult)/file(1行1ティッカー)")
        parser.add_argument("--file", help="--universe file のときのCSVパス")
        parser.add_argument("--user-id", type=int, help="ユーザー（省略時は最初の1名）")

    def handle(self, *args, **opts):
        uni = (opts.get("universe") or "today").lower()
        path = opts.get("file")
        user_id = opts.get("user_id")

        User = get_user_model()
        user = User.objects.filter(id=user_id).first() or User.objects.first()
        if not user:
            raise CommandError("ユーザーが見つかりません")

        # ポリシー取得
        policies = list(AdvisorPolicy.objects.filter(is_active=True).order_by("-priority", "id"))
        if not policies:
            raise CommandError("有効なポリシーがありません")

        # 銘柄集合
        if uni == "file":
            if not path:
                raise CommandError("--universe file には --file が必要")
            tickers = _load_csv(path)
            trs = []
            for t in tickers:
                tr = TrendResult.objects.filter(user=user, ticker=t).order_by("-asof","-updated_at").first()
                if tr: trs.append(tr)
        else:
            latest = TrendResult.objects.filter(user=user).order_by("-asof").values_list("asof", flat=True).first()
            if not latest:
                raise CommandError("TrendResult がありません。先に advisor_update_indicators を実行してください。")
            trs = list(TrendResult.objects.filter(user=user, asof=latest).order_by("-overall_score"))

        # 出力
        for tr in trs[:50]:
            entry = tr.entry_price_hint or tr.close_price or 0
            for pol in policies:
                rules = {"targets": pol.rule_json.get("targets", {}), "exits": pol.rule_json.get("exits", {})}
                xt = compute_exit_targets(policy=rules, ticker=tr.ticker, entry_price=entry, days_held=None, atr14_hint=None)
                self.stdout.write(
                    f"{tr.ticker:8s} {pol.name:16s} "
                    f"entry={entry:>6d} TP={str(xt.tp_price):>6s} SL={str(xt.sl_price):>6s} "
                    f"time_exit={xt.time_exit_due} trail={xt.trail_atr_mult} "
                )