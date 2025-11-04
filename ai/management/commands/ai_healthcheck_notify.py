from __future__ import annotations
from django.core.management.base import BaseCommand
from datetime import datetime
from ai.tasks.healthcheck import analyze_snapshot, format_ops_lines
from ai.infra.adapters.line import send_ops_alert

class Command(BaseCommand):
    help = "スナップショットの健全性を確認し、閾値超過時にLINE通知（運用向け）"

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, default=None, help='対象日 YYYY-MM-DD')
        parser.add_argument('--warn-missing', type=int, default=50, help='missing_codesがこの数を超えたら通知')
        parser.add_argument('--warn-fail', type=int, default=10, help='fetch_failuresがこの数を超えたら通知')

    def handle(self, *args, **opts):
        date_str = opts['date'] or datetime.now().date().isoformat()
        warn_missing = int(opts['warn_missing'])
        warn_fail = int(opts['warn_fail'])

        m = analyze_snapshot(date_str)
        title, lines = format_ops_lines(m)
        self.stdout.write("\n".join([f"[{title}]"]+lines))

        # 通知条件（閾値を超えたら送る）
        need = (m.get('missing_codes',0) > warn_missing) or (m.get('failures',0) > warn_fail) or (not m.get('snapshot_exists'))
        if need:
            ok, info = send_ops_alert(title, lines)
            if ok:
                self.stdout.write(self.style.SUCCESS("LINE通知: OK"))
            else:
                self.stdout.write(self.style.WARNING(f"LINE通知失敗: {info}"))