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
    path('settings/', views.settings_view, name='settings'),
]
