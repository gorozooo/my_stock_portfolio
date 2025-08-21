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

