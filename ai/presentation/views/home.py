from django.views.generic import TemplateView
from django.utils import timezone

class AIHomeView(TemplateView):
    template_name = 'ai/home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # ダミーデータ（10銘柄）
        ctx['updated_at'] = timezone.localtime().strftime('%H:%M')
        ctx['regime'] = {'label': '上昇', 'confidence': 84}  # 仮
        ctx['mode'] = {'period': '中期', 'stance': '普通'}     # 仮

        ctx['items'] = [
            {
              'name': 'トヨタ自動車', 'code': '7203',
              'sector': '自動車・輸送機',
              'score': 92, 'stars': 5,
              'trend': {'d':'up','w':'up','m':'flat'},   # ⤴️/➡️/⤵️へ変換
              'reasons': ['5>20MA上抜け','出来高+230%','週足レジ抜け','TOPIX比+2σ','（懸念）決算直後ボラ高'],
              'prices': {'entry':2450,'tp':2580,'sl':2390},
              'qty': {'shares':300,'capital':730000,'pl_plus':39000,'pl_minus':18000,'r':2.1},
            },
            # ... ×10分（同形式のダミーを増やしてOK）
        ] * 10
        return ctx