# portfolio/management/commands/advisor_snapshot.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from ...models_advisor import AdviceSession, AdviceItem, AdvisorProposal
from ...services import advisor as svc
# 既存の計算ユーティリティを使って同じKPIを作る
from ...views.home import (
    _holdings_snapshot,
    _cash_balances,
    _invested_capital,
    _stress_total_assets,
    _sum_realized_month, _sum_dividend_month,
    _sum_realized_cum, _sum_dividend_cum,
)

class Command(BaseCommand):
    help = "現在のKPI/セクターから AdviceSession/AdviceItem/AdvisorProposal を保存（学習データのスナップショット）"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=None,
                            help="参考用メモとして保存（学習期間の目安）。動作には影響しません。")
        parser.add_argument("--variant", type=str, choices=("A","B"), default="B",
                            help="助言生成のバリアント（B=policy補正, 既定=B）")
        parser.add_argument("--label", type=str, default="snapshot",
                            help="セッションnoteの先頭ラベル")
        parser.add_argument("--note", type=str, default="",
                            help="自由記述のメモを note に追記")

    def handle(self, *args, **opts):
        days: Optional[int] = opts.get("days")
        variant: str = (opts.get("variant") or "B").upper()
        label: str = opts.get("label") or "snapshot"
        extra_note: str = opts.get("note") or ""

        # --- KPI再計算（home と同ロジック） ---
        snap = _holdings_snapshot()
        cash = _cash_balances()

        total_eval_assets = int(snap["spot_mv"] + snap["margin_mv"] + cash["total"])
        unrealized_pnl = int(snap["unrealized"])

        realized_month = _sum_realized_month()
        dividend_month = _sum_dividend_month()
        realized_cum = _sum_realized_cum()
        dividend_cum = _sum_dividend_cum()

        margin_unrealized = int(snap["margin_mv"] - snap["margin_cost"])
        liquidation_value = int(snap["spot_mv"] + margin_unrealized + cash["total"])

        invested = _invested_capital()
        roi_eval_pct = (round(((total_eval_assets - invested) / invested * 100.0), 2) if invested > 0 else None)
        roi_liquid_pct = (round(((liquidation_value - invested) / invested * 100.0), 2) if invested > 0 else None)
        roi_gap_abs = (round(abs(roi_eval_pct - roi_liquid_pct), 2) if (roi_eval_pct is not None and roi_liquid_pct is not None) else None)

        gross_pos = max(int(snap["spot_mv"] + snap["margin_mv"]), 1)
        breakdown_pct = {
            "spot_pct": round(snap["spot_mv"] / gross_pos * 100, 1),
            "margin_pct": round(snap["margin_mv"] / gross_pos * 100, 1),
        }
        liquidity_rate_pct = (max(0.0, round(liquidation_value / total_eval_assets * 100, 1)) if total_eval_assets > 0 else 0.0)
        margin_ratio_pct = (round(snap["margin_mv"] / gross_pos * 100, 1) if gross_pos > 0 else 0.0)

        kpis: Dict[str, Any] = {
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
            "ab_variant": variant,  # ← どのバリアントで生成したかも残す
        }
        sectors: List[Dict[str, Any]] = snap["by_sector"]

        # 助言生成（Bだとpolicy補正）/ 特徴抽出
        ai_note, ai_items, session_id, weekly, nextmove = svc.summarize(kpis, sectors, variant=variant)
        features = svc.extract_features_for_learning(kpis, sectors)

        # セッション note
        note_pieces = [label]
        if days:
            note_pieces.append(f"days={days}")
        if extra_note:
            note_pieces.append(extra_note)
        if variant:
            note_pieces.append(f"variant={variant}")
        note_text = " / ".join(note_pieces)

        try:
            with transaction.atomic():
                sess = AdviceSession.objects.create(context_json=kpis, note=note_text)
                created = 0
                for it in ai_items:
                    kind = it.get("kind") or AdviceItem.Kind.GENERAL
                    item = AdviceItem.objects.create(
                        session=sess,
                        kind=kind,
                        message=it.get("message", ""),
                        score=float(it.get("score") or 0.0),
                        reasons=it.get("reasons") or [],
                        taken=bool(it.get("taken") or False),
                    )
                    AdvisorProposal.objects.create(
                        item=item,
                        features=features,
                        label_taken=item.taken,
                    )
                    created += 1
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"[advisor_snapshot] failed: {e}"))
            raise

        self.stdout.write(self.style.SUCCESS(
            f"AdviceSession #{sess.id} created with {created} items (variant={variant}, note='{note_text}')"
        ))