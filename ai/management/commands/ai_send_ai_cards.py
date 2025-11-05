from __future__ import annotations
from django.core.management.base import BaseCommand
from ai.services.screening import generate_top10_candidates
from ai.infra.adapters.line import send_ai_flex

class Command(BaseCommand):
    help = "AI候補上位をLINE Flexで送信（朝/昼/夕の定時通知用）"

    def add_arguments(self, parser):
        parser.add_argument('--title', type=str, default='AI候補（最新）', help='通知タイトル')
        parser.add_argument('--top', type=int, default=5, help='上位何件を送るか（1-5推奨）')

    def handle(self, *args, **opts):
        title = opts['title']
        top = int(opts['top'])
        items = []
        for c in generate_top10_candidates()[:top]:
            items.append({
                'name': c.name, 'code': c.code, 'sector': c.sector,
                'score': c.score, 'stars': c.stars,
                'trend': {'d': c.trend.d, 'w': c.trend.w, 'm': c.trend.m},
                'prices': {'entry': c.prices.entry, 'tp': c.prices.tp, 'sl': c.prices.sl},
            })
        ok, info = send_ai_flex(title, items)
        self.stdout.write(self.style.SUCCESS(f"LINE Flex send: {ok} {info}"))