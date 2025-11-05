from __future__ import annotations
import json
from pathlib import Path
from django.core.management.base import BaseCommand
from ai.services.regime import calculate_market_regime
from ai.infra.adapters.line import send_regime_flex

STATE_FILE = Path('media/regime/state.json')
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

def _load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}

def _save_state(data):
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

class Command(BaseCommand):
    help = "市場レジーム（日/週/月）を計算し、変化があればLINEにFlex通知"

    def add_arguments(self, parser):
        parser.add_argument('--delta', type=float, default=10.0, help='見出し(日足)の変化率しきい値（ポイント）')
        parser.add_argument('--force', action='store_true', help='強制送信（差分がなくても送る）')
        parser.add_argument('--title', type=str, default='市場レジーム更新', help='通知タイトル')

    def handle(self, *args, **opts):
        delta = float(opts['delta'])
        force = bool(opts['force'])
        title = opts['title']

        current = calculate_market_regime()
        prev = _load_state()

        need_send = force
        # 見出し（日足）を基準に判定
        cur_label = current['headline']['label']
        cur_pct = float(current['headline']['pct'])
        prev_label = (prev.get('headline') or {}).get('label')
        prev_pct = float((prev.get('headline') or {}).get('pct') or 0.0)

        if prev:
            if cur_label != prev_label:
                need_send = True
            elif abs(cur_pct - prev_pct) >= delta:
                need_send = True
        else:
            # 初回は保存のみ（force時は送る）
            need_send = force

        # 送信
        if need_send:
            ok, info = send_regime_flex(title, current)
            self.stdout.write(self.style.SUCCESS(f"LINE Flex: {ok} {info}"))
        else:
            self.stdout.write(self.style.WARNING("No significant change — skip LINE"))

        # 保存
        _save_state(current)
        self.stdout.write(self.style.SUCCESS(f"Saved state to {STATE_FILE}"))