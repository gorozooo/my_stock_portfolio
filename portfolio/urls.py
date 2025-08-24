from django.urls import path
from . import views

urlpatterns = [
    # --- メインページ ---
    path('', views.main_view, name='main'),

    # --- ログイン・ログアウト ---
    path('login/', views.login_view, name='login'),  # 自作ログインビュー
    path('logout/', views.logout_view, name='logout'),

    # --- 株関連ページ ---
    path('stocks/', views.stock_list_view, name='stock_list'),
    path('cash/', views.cash_view, name='cash'),
    path('realized/', views.realized_view, name='realized'),

    # --- 設定画面（親メニュー） パスワード付き（123）---
    path("settings/login/", views.settings_login, name="settings_login"),
    path('settings/', views.settings_view, name='settings'),

    # --- 設定画面の子ページ ---
    path('settings/tab_manager/', views.tab_manager_view, name='tab_manager'),        # 下タブ管理
    path('settings/theme/', views.theme_settings_view, name='theme_settings'),       # テーマ変更
    path('settings/notification/', views.notification_settings_view, name='notification_settings'),  # 通知設定
    path('settings/password/', views.settings_password_edit, name='settings_password_edit'),  # パスワード変更

    # --- 下タブ管理用API ---
    path('api/get_tabs/', views.get_tabs, name='get_tabs'),
    path('api/save_tab/', views.save_tab, name='save_tab'),
    path('api/delete_tab/<int:tab_id>/', views.delete_tab, name='delete_tab'),
    path('api/save_order/', views.save_order, name='save_order'),  # 並び順保存
]
