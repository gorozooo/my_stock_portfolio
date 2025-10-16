# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Any, Optional, Tuple, Union

from django.core.management.base import BaseCommand, CommandParser
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from ...models import Holding, RealizedTrade
from ...models_cash import BrokerAccount, CashLedger
from ...models_advisor import AdviceSession, AdviceItem
from ...services import advisor as svc_advisor

Number = Union[int, float, Decimal]

# ===== 小ユーティリティ =====
def _to_float(v: Optional[Number]) -> float:
    try:
        if v is None:
            return 0.0
        if isinstance(v, Decimal):
            return float(v)
        return float(v)
    except Exception:
        return 0.0

def _month_bounds(today: Optional[date] = None) -> Tuple[date, date]:
    d = today or date.today()
    first = d.replace(day=1)
    # 次月1日を安全に求める
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_first

# ===== データ収集 =====
def _cash_balances() -> Dict[str, Any]:
    accounts = list(BrokerAccount.objects.all().prefetch_related("ledgers"))
    cur_totals: Dict[str, int] = {}
    by_broker: Dict[str, int] = {}
    for a in accounts:
        led_sum = a.ledgers.aggregate(total=Sum("amount")).get("total") or 0
        bal = int(a.opening_balance or 0) + int(led_sum)
        currency = a.currency or "JPY"
        cur_totals[currency] = cur_totals.get(currency, 0) + bal
        by_broker[a.broker or "OTHER"] = by_broker.get(a.broker or "OTHER", 0) + bal

    by_broker_list = [{"broker": b, "cash": int(v), "currency": "JPY"} for b, v in by_broker.items()]
    return {
        "total": int(cur_totals.get("JPY", 0)),
        "by_broker": by_broker_list,
        "total_by_currency": {k: int(v) for k, v in cur_totals.items()},
    }

def _holdings_snapshot() -> dict:
    """
    - 価格は last_price 優先（無ければ avg_cost）
    - 未実現損益 = (現物+信用)評価 − (現物+信用)取得
    - セクターは Holding.sector（空は「未分類」）
    """
    holdings = list(Holding.objects.all())

    spot_mv = spot_cost = 0.0
    margin_mv = margin_cost = 0.0
    sector_map: Dict[str, Dict[str, float]] = {}

    for h in holdings:
        qty = _to_float(getattr(h, "quantity", 0))
        unit = _to_float(getattr(h, "avg_cost", 0))
        price = _to_float(getattr(h, "last_price", None)) or unit
        mv = price * qty
        cost = unit * qty

        acc = (getattr(h, "account", "") or "").upper()
        sector = (getattr(h, "sector", None) or "").strip() or "未分類"

        if acc == "MARGIN":
            margin_mv += mv
            margin_cost += cost
        else:
            spot_mv += mv
            spot_cost += cost

        rec = sector_map.setdefault(sector, {"mv": 0.0, "cost": 0.0})
        rec["mv"] += mv
        rec["cost"] += cost

    total_unrealized_pnl = (spot_mv + margin_mv) - (spot_cost + margin_cost)

    # 勝率（全期間の実現）
    qs = RealizedTrade.objects.all()
    win = sum(1 for r in qs if _to_float(getattr(r, "pnl", 0)) > 0)
    lose = sum(1 for r in qs if _to_float(getattr(r, "pnl", 0)) < 0)
    total_trades = win + lose
    win_ratio = round((win / total_trades * 100.0) if total_trades else 0.0, 1)

    by_sector: List[Dict[str, Any]] = []
    total_mv_for_share = sum(max(0.0, rec["mv"]) for rec in sector_map.values()) or 1.0
    for sec, d in sector_map.items():
        mv, cost = d["mv"], d["cost"]
        perf = ((mv - cost) / cost * 100.0) if cost > 0 else 0.0
        share = mv / total_mv_for_share * 100.0
        by_sector.append({"sector": sec, "mv": round(mv), "rate": round(perf, 2), "share_pct": round(share, 1)})
    by_sector.sort(key=lambda x: x["mv"], reverse=True)

    return dict(
        spot_mv=round(spot_mv),
        spot_cost=round(spot_cost),
        margin_mv=round(margin_mv),
        margin_cost=round(margin_cost),
        unrealized=round(total_unrealized_pnl),
        win_ratio=win_ratio,
        by_sector=by_sector[:10],
    )

def _sum_realized_month() -> int:
    first, next_first = _month_bounds()
    qs = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.REALIZED,
        at__gte=first, at__lt=next_first,
    )
    return int(sum(int(x.amount) for x in qs))

def _sum_dividend_month() -> int:
    first, next_first = _month_bounds()
    qs = CashLedger.objects.filter(
        source_type=CashLedger.SourceType.DIVIDEND,
        at__gte=first, at__lt=next_first,
    )
    return int(sum(int(x.amount) for x in qs))

def _sum_realized_cum() -> int:
    return int(
        CashLedger.objects.filter(source_type=CashLedger.SourceType.REALIZED)
        .aggregate(s=Sum("amount")).get("s") or 0
    )

def _sum_dividend_cum() -> int:
    return int(
        CashLedger.objects.filter(source_type=CashLedger.SourceType.DIVIDEND)
        .aggregate(s=Sum("amount")).get("s") or 0
    )

def _invested_capital() -> int:
    opening = int(BrokerAccount.objects.aggregate(total=Sum("opening_balance")).get("total") or 0)
    dep = int(CashLedger.objects.filter(kind=CashLedger.Kind.DEPOSIT).aggregate(s=Sum("amount")).get("s") or 0)
    xin = int(CashLedger.objects.filter(kind=CashLedger.Kind.XFER_IN).aggregate(s=Sum("amount")).get("s") or 0)
    wdr = int(CashLedger.objects.filter(kind=CashLedger.Kind.WITHDRAW).aggregate(s=Sum("amount")).get("s") or 0)
    xout= int(CashLedger.objects.filter(kind=CashLedger.Kind.XFER_OUT).aggregate(s=Sum("amount")).get("s") or 0)
    return int(opening + dep + xin - wdr - xout)

# ===== KPI構築 =====
def build_kpis() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    snap = _holdings_snapshot()
    cash = _cash_balances()

    total_eval_assets = int(snap["spot_mv"] + snap["margin_mv"] + cash["total"])
    unrealized_pnl = int(snap["unrealized"])

    realized_month = _sum_realized_month()
    dividend_month = _sum_dividend_month()
    realized_cum = _sum_realized_cum()
    dividend_cum = _sum_dividend_cum()

    # 信用は含み損益のみ現金化
    margin_unrealized = int(snap["margin_mv"] - snap["margin_cost"])
    liquidation_value = int(snap["spot_mv"] + margin_unrealized + cash["total"])

    invested = _invested_capital()

    roi_eval_pct = round(((total_eval_assets - invested) / invested * 100.0), 2) if invested > 0 else None
    roi_liquid_pct = round(((liquidation_value - invested) / invested * 100.0), 2) if invested > 0 else None
    roi_gap_abs = round(abs(roi_eval_pct - roi_liquid_pct), 2) if (roi_eval_pct is not None and roi_liquid_pct is not None) else None

    gross_pos = max(int(snap["spot_mv"] + snap["margin_mv"]), 1)
    liquidity_rate_pct = max(0.0, round(liquidation_value / total_eval_assets * 100, 1)) if total_eval_assets > 0 else 0.0
    margin_ratio_pct = round(snap["margin_mv"] / gross_pos * 100, 1) if gross_pos > 0 else 0.0

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
        # この実行は policy補正で動かす想定
        "ab_variant": "B",
    }
    sectors = snap["by_sector"]
    return kpis, sectors

def _format_mail(kpis: Dict[str, Any], items: List[Dict[str, Any]]) -> str:
    lines = []
    lines.append(f"🧠 AIモーニング助言（{timezone.now():%Y-%m-%d %H:%M}）")
    lines.append("")
    lines.append(f"総資産: ¥{kpis.get('total_assets',0):,}")
    lines.append(f"評価ROI: {('--' if kpis.get('roi_eval_pct') is None else f'{kpis['roi_eval_pct']:.2f}%')}  /  現金ROI: {('--' if kpis.get('roi_liquid_pct') is None else f'{kpis['roi_liquid_pct']:.2f}%')}")
    if kpis.get("roi_gap_abs") is not None:
        lines.append(f"ROI乖離: {kpis['roi_gap_abs']:.1f}pt")
    lines.append(f"現金: ¥{kpis.get('cash_total',0):,} / 流動性: {kpis.get('liquidity_rate_pct',0):.1f}% / 信用比率: {kpis.get('margin_ratio_pct',0):.1f}%")
    lines.append("")
    lines.append("▶ 本日の提案（上位）")
    if not items:
        lines.append("・提案なし")
    else:
        for it in items[:5]:
            chk = "✅" if it.get("taken") else "☐"
            score = float(it.get("score") or 0.0)
            lines.append(f"{chk} {it.get('message','')}  (優先度 {score:.2f})")
    return "\n".join(lines)

# ====== コマンド ======
class Command(BaseCommand):
    help = "policy.json を反映して助言を生成・セッション保存し、（任意）メール送信します。"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--email", type=str, default="", help="送信先（カンマ区切り）。未指定なら送信しない")
        parser.add_argument("--subject", type=str, default="AIモーニング助言", help="メール件名")
        parser.add_argument("--dry-run", action="store_true", help="DB保存を行わず試行（メール文面は出力）")

    def handle(self, *args, **opts):
        to = [x.strip() for x in (opts["email"] or "").split(",") if x.strip()]
        subject = opts["subject"]
        dry = bool(opts["dry_run"])

        # 1) KPI + セクター計算
        kpis, sectors = build_kpis()

        # 2) 助言生成（variant=B → policy補正込み）
        ai_note, ai_items, ai_session_id, weekly_draft, nextmove_draft = svc_advisor.summarize(kpis, sectors, variant="B")

        # 3) ROI乖離が大きい時は先頭へ
        if kpis.get("roi_gap_abs") is not None and kpis["roi_gap_abs"] >= 20:
            key = "評価ROIと現金ROIの乖離が"
            idx = next((i for i, x in enumerate(ai_items) if key in x.get("message","")), None)
            if idx not in (None, 0):
                ai_items.insert(0, ai_items.pop(idx))

        # 4) 永続化
        if not dry:
            try:
                ai_items = svc_advisor.ensure_session_persisted(ai_note or "", ai_items, kpis, variant="B")
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"[WARN] session save failed: {e}"))

        # 5) メール or 標準出力
        body = _format_mail(kpis, ai_items)
        if to:
            send_mail(
                subject=subject,
                message=body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
                recipient_list=to,
                fail_silently=False,
            )
            self.stdout.write(self.style.SUCCESS(f"[advisor_run] mail sent → {', '.join(to)}"))
        else:
            self.stdout.write(body)

        # 6) ログ
        saved = "（dry-run）" if dry else "saved"
        self.stdout.write(self.style.SUCCESS(f"[advisor_run] done {saved}. items={len(ai_items)}"))