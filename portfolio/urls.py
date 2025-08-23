from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.main_view, name='main'),  # メインページ
    path('login/', views.login_view, name='login'),  # ← 自作ログインビューに変更
    path('logout/', views.logout_view, name='logout'), 
    path('stocks/', views.stock_list_view, name='stock_list'),
    path('cash/', views.cash_view, name='cash'),
    path('realized/', views.realized_view, name='realized'),
    path("settings/login/", views.settings_login, name="settings_login"),
    path('settings/', views.settings_view, name="settings"),
    path("api/tabs/", views.get_tabs, name="get_tabs"),
    path("api/tabs/save/", views.save_tab, name="save_tab"),
    path("api/tabs/<int:tab_id>/delete/", views.delete_tab, name="delete_tab"),
    path('settings/password/', views.settings_password_edit, name="settings_password_edit"),
]
