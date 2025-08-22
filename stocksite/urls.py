from django.contrib import admin
from django.urls import path
from portfolio import views as pf_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", pf_views.main_view, name="main"),
    path("login/", pf_views.login_view, name="login"),
    path("logout/", pf_views.logout_view, name="logout"),
]
