from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)

PAGES = {
    "main": {"name": "ホーム", "icon": "🏠", "url_name": "main"},
    "stock_list": {"name": "株", "icon": "📊", "url_name": "stock_list"},
    "cash_list": {"name": "キャッシュ", "icon": "💰", "url_name": "cash_list"},
    "realized_list": {"name": "実現損益", "icon": "📈", "url_name": "realized_list"},
    "setting": {"name": "設定", "icon": "⚙️", "url_name": "setting"},
}
