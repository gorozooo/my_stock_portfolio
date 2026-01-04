from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from advisor.models_order import OrderMemo

class Command(BaseCommand):
    help = "OrderMemo の直近を表示"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)

    def handle(self, *args, **opts):
        U = get_user_model()
        u = U.objects.first()
        if not u:
            self.stdout.write("no user"); return
        qs = OrderMemo.objects.filter(user=u).order_by("-created_at")[:opts["limit"]]
        for m in qs:
            self.stdout.write(
                f"{m.created_at:%Y-%m-%d %H:%M}  {m.ticker}  {m.name}  "
                f"entry={m.entry_price}  tp={m.tp_price}  sl={m.sl_price}  {m.policies_line}"
            )