# aiapp/urls.py
from django.urls import path

from .views.dashboard import dashboard, toggle_mode
from .views.picks import picks, picks_json, picks_simulate
from .views.api import picks_rebuild  # ← LIVE/DEMO再生成API
from .views.settings import settings_view  # ← AI設定画面

app_name = "aiapp"

urlpatterns = [
    # メインダッシュボード
    path("", dashboard, name="dashboard"),
    path("toggle/", toggle_mode, name="toggle"),

    # AIピック画面（スナップショット読込専用）
    path("picks/", picks, name="picks"),

    # LIVE/DEMOボタン → 非同期再生成API
    path("api/picks/rebuild", picks_rebuild, name="picks_rebuild"),

    # ピックデータJSON
    path("picks.json", picks_json, name="picks_json"),

    # AI設定画面（リスク％・将来拡張用）
    path("settings/", settings_view, name="settings"),

    # シミュレ（紙トレ保存）
    path("picks/simulate/", picks_simulate, name="ai_picks_simulate"),
]