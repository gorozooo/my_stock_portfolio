from django.utils import timezone

def ui(request):
    menus = [
        {"name": "ホーム", "url_name": "main", "icon": "🏠", "bottom": True},
        # 今後 stock_list, setting を追加予定
    ]
    return {
        "MENUS": menus,
        "LAST_UPDATED": timezone.now(),
    }
