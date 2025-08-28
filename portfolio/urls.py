from django.urls import path
from . import views

urlpatterns = [
    # --- メインページ ---
    path('', views.main_view, name='main'),

    # --- ログイン・ログアウト ---
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # --- 株関連ページ ---
    path('stocks/', views.stock_list_view, name='stock_list'),
    path('stocks/new/', views.stock_create, name='stock_create'),
    path('cash/', views.cash_view, name='cash'),
    path('realized/', views.realized_view, name='realized'),
    path('trade_history/', views.trade_history, name='trade_history'),

    # --- 株関連 API ---
    path('stocks/api/stock_by_code/', views.get_stock_by_code, name='stock_by_code'),
    path('stocks/api/suggest_name/', views.suggest_stock_name, name='suggest_name'),
    path('stocks/api/sectors/', views.get_sector_list, name='sector_list'),

    # --- 設定画面（親メニュー） パスワード付き ---
    path('settings/login/', views.settings_login, name='settings_login'),
    path('settings/', views.settings_view, name='settings'),

    # --- 設定画面の子ページ ---
    path('settings/tab_manager/', views.tab_manager_view, name='tab_manager'),
    path('settings/theme/', views.theme_settings_view, name='theme_settings'),
    path('settings/notification/', views.notification_settings_view, name='notification_settings'),
    path('settings/password/', views.settings_password_edit, name='settings_password_edit'),

    # --- 下タブ管理用API ---
    path('api/get_tabs/', views.get_tabs, name='get_tabs'),
    path('api/save_tab/', views.save_tab, name='save_tab'),
    path('api/delete_tab/<int:tab_id>/', views.delete_tab, name='delete_tab'),
    path('api/save_order/', views.save_order, name='save_order'),
]
