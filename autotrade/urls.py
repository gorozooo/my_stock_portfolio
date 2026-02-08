# autotrade/urls.py
from django.urls import path
from . import views

app_name = "autotrade"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    # --- Tuning ---
    path("tuning/", views.tuning_list, name="tuning_list"),

    # --- API ---
    path("api/emergency-stop/", views.api_emergency_stop, name="api_emergency_stop"),
]