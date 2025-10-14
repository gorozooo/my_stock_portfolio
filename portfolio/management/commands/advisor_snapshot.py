# -*- coding: utf-8 -*-
from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils import timezone

# 既存ロジックを流用
from ...views.home import (
    _holdings_snapshot, _cash_balances,
    _sum_realized_month, _sum_dividend_month,
    _sum_realized_cum, _sum_dividend_cum,
    _invested_capital, _stress_total_assets,
)
from ...services import advisor as svc_advisor


def _build_kpis():
    snap = _holdings_snapshot()
    cash = _cash_balances()

    total_eval_assets = int(snap["spot_mv"] + snap["margin_mv"] + cash["total"])
    unrealized_pnl = int(snap["unrealized"])

    realized_month = _sum_realized_month()
    dividend_month = _sum_dividend_month()
    realized_cum   = _sum_realized_cum()
    dividend_cum   = _sum_dividend_cum()

    margin_unrealized = int(snap["margin_mv"] - snap["margin_cost"])
    liquidation_value = int(snap["spot_mv"] + margin_unrealized + cash["total"])

    invested = _invested_capital()

    roi_eval_pct   = round(((total_eval_assets - invested) / invested * 100.0), 2) if invested > 0 else None
    roi_liquid_pct = round(((liquidation_value - invested) / invested * 100.0), 2) if invested > 0 else None
    roi_gap_abs = round(abs(roi_eval_pct - roi_liquid_pct), 2) if (roi_eval_pct is not None and roi_liquid_pct is not None) else None

    gross_pos = max(int(snap["spot_mv"] + snap["margin_mv"]), 1)
    breakdown_pct = {
        "spot_pct":   round(snap["spot_mv"] / gross_pos * 100, 1),
        "margin_pct": round(snap["margin_mv"] / gross_pos * 100, 1),
    }

    liquidity_rate_pct = max(0.0, round(liquidation_value / total_eval_assets * 100, 1)) if total_eval_assets > 0 else 0.0
    margin_ratio_pct   = round(snap["margin_mv"] / gross_pos * 100, 1) if gross_pos > 0 else 0.0

    kpis = {
        "total_assets": total_eval_assets,
        "unrealized_pnl": unrealized_pnl,
        "realized_month": realized_month,
        "dividend_month": dividend_month,
        "realized_cum": realized_cum,
        "dividend_cum": dividend_cum,
        "cash_total": cash["total"],
        "liquidation": liquidation_value,
        "invested": invested,
        "roi_eval_pct": roi_eval_pct,
        "roi_liquid_pct": roi_liquid_pct,
        "roi_gap_abs": roi_gap_abs,
        "win_ratio": snap["win_ratio"],
        "liquidity_rate_pct": liquidity_rate_pct,
        "margin_ratio_pct": margin_ratio_pct,
        "margin_unrealized": margin_unrealized,
    }
    sectors = snap["by_sector"]
    return kpis, sectors


class Command(BaseCommand):
    help = "AIアドバイザー: 現在のKPI/保有スナップショットからセッション(AdviceSession/AdviceItem)を明示保存します。"

    def add_arguments(self, parser):
        parser.add_argument("--tag", type=str, default="", help="セッション注記に付ける任意タグ（例: 'daily-cron'）")
        parser.add_argument("--force", action="store_true", help="クールダウンを回避して必ず新規セッションを作成する")
        parser.add_argument("--dry-run", action="store_true", help="保存せず内容をプレビューする")

    def handle(self, *args, **opts):
        now = timezone.now()
        kpis, sectors = _build_kpis()

        # 既存の生成器をそのまま利用
        ai_note, ai_items, session_id, weekly, nextmove = svc_advisor.summarize(kpis, sectors)

        # --force のときは ai_note に時刻タグを足してハッシュを変え、確実に新規保存にする
        if opts["force"]:
            ai_note = f"{ai_note} [{now:%Y-%m-%d %H:%M}]"
        if opts["tag"]:
            ai_note = f"{ai_note} ({opts['tag']})"

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("[DRY RUN] 以下を保存予定:"))
            self.stdout.write(f"  note: {ai_note}")
            for i, it in enumerate(ai_items, 1):
                self.stdout.write(f"   - ({i}) score={it.get('score'):.2f} taken={it.get('taken')} : {it.get('message')}")
            return

        # 明示保存（内部で重複保存ガードあり／force時はnote変更で回避）
        saved_items = svc_advisor.ensure_session_persisted(ai_note, ai_items, kpis)

        saved = sum(1 for _ in saved_items)
        self.stdout.write(self.style.SUCCESS(
            f"[{now:%Y-%m-%d %H:%M}] セッション保存完了: items={saved} note='{ai_note[:80]}'"
        ))