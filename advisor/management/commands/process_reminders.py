from django.core.management.base import BaseCommand
from django.utils import timezone
from advisor.models import Reminder
import os, requests

LINE_TOKEN = os.getenv("LINE_NOTIFY_TOKEN")  # 後で入れる
LINE_API = "https://notify-api.line.me/api/notify"

def send_line(message):
    if not LINE_TOKEN:
        print("[DRYRUN] LINE:", message)
        return True
    try:
        res = requests.post(
            LINE_API,
            headers={"Authorization": f"Bearer {LINE_TOKEN}"},
            data={"message": message},
            timeout=10
        )
        print("LINE status", res.status_code, res.text)
        return res.status_code == 200
    except Exception as e:
        print("LINE error", e)
        return False

class Command(BaseCommand):
    help = "Fire due reminders"

    def handle(self, *args, **opts):
        now = timezone.now()
        qs = Reminder.objects.filter(done=False, fire_at__lte=now).order_by("fire_at")[:50]
        for r in qs:
            msg = f"⏰ リマインド：{r.ticker} をもう一度チェック"
            ok = send_line(msg)
            if ok:
                r.done = True
                r.save(update_fields=["done"])
        self.stdout.write(self.style.SUCCESS(f"processed {qs.count()} reminders"))