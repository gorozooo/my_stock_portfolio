# portfolio/urls.py
from django.urls import path
from .views import trend_api, trend_page, trend_card_partial

app_name = "portfolio"

urlpatterns = [
    path("trend/", trend_page, name="trend"),
    path("trend/card", trend_card_partial, name="trend_card"),  # htmx 用
    path("api/trend", trend_api, name="trend_api"),             # JSON API
]
