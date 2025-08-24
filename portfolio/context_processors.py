from django.utils import timezone
from .utils import get_bottom_tabs

def ui(request):
    menus = [
        {"name": "ホーム", "url_name": "main", "icon": "🏠", "bottom": True},
    ]
    jst_now = timezone.localtime(timezone.now())
    formatted_time = jst_now.strftime("%Y.%m.%d %H:%M")

    return {
        "MENUS": menus,
        "LAST_UPDATED": formatted_time,
    }

def bottom_tabs(request):
    """全ページで共通の下タブを取得"""
    return {"BOTTOM_TABS": get_bottom_tabs()}
