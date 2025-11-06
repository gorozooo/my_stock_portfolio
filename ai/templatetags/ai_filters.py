# ai/templatetags/ai_filters.py
from django import template
import re
from ai.models import TrendResult

register = template.Library()

@register.filter
def sector_jp_safe(sector_value, code):
    """
    表示用の保険フィルタ：
    - sector_value が数値/空/おかしい → TrendResult.sector_jp を返す
    - code は '8035' など4桁 or '8035.T' でもOK
    """
    s = '' if sector_value is None else str(sector_value).strip()
    # sectorが純数値（2050.0等）や空なら差し替え
    if s == '' or re.fullmatch(r'[0-9.]+', s):
        m = re.search(r'(\d{4})', str(code or ''))
        c4 = m.group(1) if m else None
        if c4:
            tr = TrendResult.objects.filter(code=c4).only('sector_jp').first()
            if tr and tr.sector_jp:
                return tr.sector_jp
        return '-'  # どうしても見つからない保険
    return s