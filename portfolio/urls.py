from django.urls import path
from . import views
from portfolio import views as pf_views

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
    
    # --- 株モーダル専用ページ ---
    path("stocks/<int:pk>/detail_fragment/", views.stock_detail_fragment, name="stock_detail_fragment"),
    path("stocks/<int:pk>/overview.json", views.stock_overview_json, name="stock_overview_json"),
    path("stocks/<int:pk>/price.json", views.stock_price_json, name="stock_price_json"),
    path("stocks/<int:pk>/fundamental.json", views.stock_fundamental_json, name="stock_fundamental_json"),
    path("stocks/<int:pk>/fundamental.json", pf_views.stock_fundamental_json, name="stock_fundamental_json"),
    path("stocks/<int:pk>/news.json", pf_views.stock_news_json, name="stock_news_json"),
    
    # --- 株編集・売却（専用ページ） ---
    path("stocks/<int:pk>/edit/", views.edit_stock_page, name="stock_edit"),   # 編集ページ
    path("stocks/<int:pk>/edit/fragment/", views.edit_stock_fragment, name="edit_stock_frag"),  # モーダル用
    path("stocks/<int:pk>/sell/", views.sell_stock_page, name="stock_sell"),   # 売却ページ

    # --- 株関連 API ---
    path('stocks/api/stock_by_code/', views.get_stock_by_code, name='stock_by_code'),
    path('stocks/api/suggest_name/', views.suggest_stock_name, name='suggest_name'),
    path('stocks/api/sectors/', views.get_sector_list, name='sector_list'),
    
    # --- 配当入力 ---
    path("dividend/new/", views.dividend_new_page, name="dividend_new"),
    
    # --- 入出金 ---
    path("cashflow/new/", views.cashflow_create, name="cashflow_create"),   # 入出金
    
    # --- 登録ページ ---
    path("register/", views.register_hub, name="register_hub"),
    
    # --- 設定画面（親メニュー） ---
    path('settings/login/', views.settings_login, name='settings_login'),
    path('settings/', views.settings_view, name='settings'),

    # --- 設定画面の子ページ ---
    path('settings/tab_manager/', views.tab_manager_view, name='tab_manager'),
    path('settings/theme/', views.theme_settings_view, name='theme_settings'),
    path('settings/notification/', views.notification_settings_view, name='notification_settings'),
    path('settings/password/', views.settings_password_edit, name='settings_password_edit'),

    # --- 下タブ管理用 API ---
    path("tabs/save/", views.save_tab, name="save_tab"),
    path("tabs/delete/<int:tab_id>/", views.delete_tab, name="delete_tab"),
    path("tabs/reorder/", views.save_order, name="save_order"),
    path("submenus/save/", views.save_submenu, name="save_submenu"),
    path("submenus/delete/<int:sub_id>/", views.delete_submenu, name="delete_submenu"),
    path("submenus/reorder/", views.save_submenu_order, name="save_submenu_order"),
]