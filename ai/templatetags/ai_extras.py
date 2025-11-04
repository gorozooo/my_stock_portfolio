from django import template
register = template.Library()

@register.filter
def trend_icon(val: str) -> str:
    return '⤴️' if val == 'up' else ('⤵️' if val == 'down' else '➡️')