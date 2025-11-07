from django.urls import path
from .views.dashboard import dashboard, toggle_mode
from .views.picks import picks

app_name = "aiapp"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("toggle/", toggle_mode, name="toggle"),
    path("picks/", picks, name="picks"),
]
