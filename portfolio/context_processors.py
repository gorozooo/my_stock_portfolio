# portfolio/context_processors.py
from django.utils.timezone import localtime
from django.contrib.auth.models import User
from .models import Stock  # 例としてStockモデルの更新日時

def last_updated(request):
    data = {}
    if request.user.is_authenticated:
        data['username'] = request.user.username
        data['last_login'] = localtime(request.user.last_login)
        # ここでモデルの最終更新を取得
        latest_stock = Stock.objects.order_by('-updated_at').first()
        data['model_last_updated'] = localtime(latest_stock.updated_at) if latest_stock else None
    else:
        data['username'] = 'Guest'
        data['last_login'] = None
        data['model_last_updated'] = None
    return data

def page_info(request):
    return {
        "PAGES": {
            "home": {"icon": "🏠", "name": "ホーム", "url": "main"},
            "stock_list": {"icon": "📊", "name": "株一覧", "url": "stock_list"},
            "settings": {"icon": "⚙️", "name": "設定", "url": "settings"},
        }
    }

from .models import Menu  # DBに登録する場合
def bottom_nav_items(request):
    # DBで管理する場合
    items = Menu.objects.filter(parent=None).order_by('order')
    result = []
    for item in items:
        submenus = item.children.all().order_by('order')  # related_name='children'
        result.append({
            'name': item.name,
            'icon': item.icon,
            'url_name': item.url_name,
            'submenu': [{'name': sub.name, 'url_name': sub.url_name} for sub in submenus]
        })
    return {'bottom_nav_items': result}

from .models import Menu  # Menuモデルからメニュー情報を取得する場合

def bottom_nav_pages(request):
    """
    下タブナビゲーション用のページリストを全ページ分返す
    """
    # 仮にDBから取得する場合
    pages = Menu.objects.filter(parent__isnull=True).order_by('order')
    
    return {
        'bottom_nav_pages': pages
    }

from .models import Menu  # Menu モデルはDBのナビメニュー用

def bottom_nav_items(request):
    # 親メニューだけ取得（親子構造）
    parents = Menu.objects.filter(parent__isnull=True).order_by('order')

    # 子メニューを付与
    items = []
    for p in parents:
        sub_items = list(Menu.objects.filter(parent=p).order_by('order'))
        items.append({
            'name': p.name,
            'icon': p.icon,
            'url_name': p.url_name,
            'submenu': sub_items if sub_items else None,
        })
    return {'bottom_nav_items': items}