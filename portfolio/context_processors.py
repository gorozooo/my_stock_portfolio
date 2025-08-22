from django.utils import timezone
from .models import BottomTab

def ui(request):
    menus = [
        {"name": "ホーム", "url_name": "main", "icon": "🏠", "bottom": True},
        # 今後 stock_list, setting を追加予定
    ]
    # 現在時刻を日本時間に変換してからフォーマット
    jst_now = timezone.localtime(timezone.now())
    formatted_time = jst_now.strftime("%Y.%m.%d %H:%M")

    return {
        "MENUS": menus,
        "LAST_UPDATED": formatted_time,
    }

def bottom_tabs(request):
    tabs = BottomTab.objects.all().order_by('order')
    return {'BOTTOM_TABS': tabs}