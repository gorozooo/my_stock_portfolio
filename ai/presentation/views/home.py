from __future__ import annotations
from django.views.generic import TemplateView
from django.utils import timezone

from ai.services.screening import generate_top10_candidates
from ai.services.regime import calculate_market_regime

class AIHomeView(TemplateView):
    template_name = 'ai/home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['updated_at'] = timezone.localtime().strftime('%H:%M')

        regime = calculate_market_regime()
        ctx['regime'] = regime.get('headline', regime)
        ctx['mode'] = {'period': '中期', 'stance': '普通'}

        # Candidate → dict へ（テンプレ側でそのまま表示できる形）
        items = []
        for c in generate_top10_candidates():
            items.append({
                'name': c.name,
                'code': c.code,
                # ★sector はここで“日本語名”を保証済み
                'sector': c.sector,
                'score': c.score,
                'stars': c.stars,
                'trend': {'d': c.trend.d, 'w': c.trend.w, 'm': c.trend.m},
                'reasons': list(c.reasons),
                'prices': {'entry': c.prices.entry, 'tp': c.prices.tp, 'sl': c.prices.sl},
                'qty':    {'shares': c.qty.shares, 'capital': c.qty.capital,
                           'pl_plus': c.qty.pl_plus, 'pl_minus': c.qty.pl_minus, 'r': c.qty.r},
            })
        ctx['items'] = items

        # 防御（ここでセクターが数値/空なら即検知）
        def _bad(x):
            s = str(x.get('sector','')).strip()
            import re
            return (not s) or s == '-' or re.fullmatch(r'\d+(?:\.\d+)?', s) is not None
        ctx['__has_bad_sector__'] = any(_bad(i) for i in items)

        return ctx