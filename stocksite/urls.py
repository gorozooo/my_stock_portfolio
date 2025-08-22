from django.contrib import admin
from django.urls import path
from portfolio import views as pf_views
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", pf_views.login_view, name="login"),
    path("logout/", pf_views.logout_view, name="logout"),
    path('', include('portfolio.urls')),  # アプリの URL を反映
]
