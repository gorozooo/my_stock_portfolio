from django.views.generic import TemplateView
from django.utils import timezone
from ai.services.screening import generate_top10_candidates
from ai.services.regime import calculate_market_regime

class AIHomeView(TemplateView):
    template_name = 'ai/home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['updated_at'] = timezone.localtime().strftime('%H:%M')
        ctx['regime'] = calculate_market_regime()
        ctx['mode'] = {'period': '中期', 'stance': '普通'}

        items = []
        for c in generate_top10_candidates():
            items.append({
                'name': c.name,
                'code': c.code,
                'sector': c.sector,
                'score': c.score,
                'stars': c.stars,  # ★ confidence→⭐️済
                'trend': {'d': c.trend.d, 'w': c.trend.w, 'm': c.trend.m},
                'reasons': c.reasons,
                'prices': {'entry': c.prices.entry, 'tp': c.prices.tp, 'sl': c.prices.sl},
                'qty': {
                    'shares': c.qty.shares,
                    'capital': c.qty.capital,
                    'pl_plus': c.qty.pl_plus,
                    'pl_minus': c.qty.pl_minus,
                    'r': c.qty.r
                },
            })
        ctx['items'] = items
        return ctx