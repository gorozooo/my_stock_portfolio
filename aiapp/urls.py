# aiapp/urls.py
from django.urls import path
from .views.dashboard import dashboard, toggle_mode
from .views.picks import picks
from .views.api import picks_rebuild  # ← 新規追加

app_name = "aiapp"

urlpatterns = [
    # メインダッシュボード
    path("", dashboard, name="dashboard"),
    path("toggle/", toggle_mode, name="toggle"),

    # AIピック画面（スナップショット読込専用）
    path("picks/", picks, name="picks"),

    # LIVE/DEMOボタン → 非同期再生成API
    path("api/picks/rebuild", picks_rebuild, name="picks_rebuild"),

    path("picks.json", picks_json, name="picks_json"),
    
]