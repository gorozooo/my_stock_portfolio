# portfolio/management/commands/snapshot_today_pnl.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Sum
from portfolio.models import TodayPnLSnapshot

# 既存モデルを安全に import
try:
    from portfolio.models import RealizedProfit
except Exception:
    RealizedProfit = None
try:
    from portfolio.models import Dividend
except Exception:
    Dividend = None
try:
    from portfolio.models import CashFlow
except Exception:
    CashFlow = None

class Command(BaseCommand):
    help = "当日の損益（売買/配当/現金入出金の当日分）を集計してスナップショット保存する。"

    def handle(self, *args, **options):
        today = timezone.localdate()
        pnl_today = 0

        # 売買：当日分
        if RealizedProfit:
            q = RealizedProfit.objects.filter(date=today)
            val = q.aggregate(s=Sum("profit_amount")).get("s") or 0
            pnl_today += int(val)

        # 配当：当日分（net があれば net、なければ gross - tax）
        if Dividend:
            dq = Dividend.objects.filter(received_at=today)
            total_div = 0
            for d in dq:
                if getattr(d, "net_amount", None) is not None:
                    total_div += int(d.net_amount or 0)
                else:
                    total_div += int((d.gross_amount or 0) - (d.tax or 0))
            pnl_today += total_div

        # 現金入出金：当日分（任意：日中の入出金を当日損益に含めたい場合）
        cash_today = 0
        if CashFlow:
            cq = CashFlow.objects.filter(occurred_at=today)
            agg = cq.values("flow_type").annotate(total=Sum("amount"))
            for row in agg:
                amt = int(row.get("total") or 0)
                cash_today += amt if (row.get("flow_type") == "in") else -amt
        # 方針：当日の純増減を足す場合は下を有効に
        pnl_today += cash_today

        # ベンチマーク差は任意（実装済みなら取り込む）
        bench_ret = None  # ％。必要になったらここで算出

        obj, created = TodayPnLSnapshot.objects.update_or_create(
            date=today,
            defaults={"pnl_today": pnl_today, "bench_ret": bench_ret},
        )
        self.stdout.write(self.style.SUCCESS(
            f"[snapshot_today_pnl] {today} -> pnl={pnl_today} (bench={bench_ret}) {'created' if created else 'updated'}"
        ))