#autotrade/urls.py
from django.urls import path
from . import views

app_name = "autotrade"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/emergency-stop/", views.api_emergency_stop, name="api_emergency_stop"),
]