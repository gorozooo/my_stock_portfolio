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
        
@register.filter
def months_from_days(value):
    """日数からおおよその月数（30日換算）を返す"""
    try:
        days = int(value)
        months = round(days / 30)
        return f"{days}日（約{months}ヶ月）"
    except Exception:
        return "-"