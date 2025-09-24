# portfolio/templatetags/num.py
from django import template
from decimal import Decimal

register = template.Library()

@register.filter
def mul(a, b):
    """掛け算フィルタ: {{ x|mul:y }}"""
    try:
        return Decimal(a) * Decimal(b)
    except Exception:
        return 0