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

        # ---- 通常のAI候補生成 ----
        items = []
        for c in generate_top10_candidates()[:top]:
            items.append({
                'name': c.name,
                'code': c.code,
                'sector': c.sector,
                'score': c.score,
                'stars': c.stars,
                'trend': {'d': c.trend.d, 'w': c.trend.w, 'm': c.trend.m},
                'prices': {
                    'entry': c.prices.entry,
                    'tp': c.prices.tp,
                    'sl': c.prices.sl
                },
            })

        # ---- ダミーデータ（0件時のみ注入）----
        if not items:
            self.stdout.write(self.style.WARNING("No candidates found — using dummy data for test."))
            items = [
                {
                    'name': '東京エレクトロン',
                    'code': '8035',
                    'sector': '電気機器',
                    'score': 85,
                    'stars': 5,
                    'trend': {'d': 'up', 'w': 'up', 'm': 'flat'},
                    'prices': {'entry': 23500, 'tp': 24400, 'sl': 22900}
                },
                {
                    'name': 'トヨタ自動車',
                    'code': '7203',
                    'sector': '自動車・輸送機',
                    'score': 78,
                    'stars': 4,
                    'trend': {'d': 'flat', 'w': 'up', 'm': 'up'},
                    'prices': {'entry': 2680, 'tp': 2790, 'sl': 2610}
                },
                {
                    'name': '日本電信電話',
                    'code': '9432',
                    'sector': '情報・通信',
                    'score': 72,
                    'stars': 3,
                    'trend': {'d': 'up', 'w': 'flat', 'm': 'down'},
                    'prices': {'entry': 190.5, 'tp': 199.5, 'sl': 186.0}
                },
            ]

        # ---- Flex送信 ----
        ok, info = send_ai_flex(title, items)
        self.stdout.write(self.style.SUCCESS(f"LINE Flex send: {ok} {info}"))