from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.main, name='main'),  # メインページ
    path('login/', views.login_view, name='login'),  # ← 自作ログインビューに変更
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('stocks/', views.stock_list, name='stock_list'),
    path('setting/', views.setting, name='setting'),
]
