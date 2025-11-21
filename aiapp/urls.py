from django.urls import path
from .views.dashboard import dashboard, toggle_mode
from .views.picks import picks, picks_json
from .views.api import picks_rebuild
from .views.settings import settings_view
from .views.simulate import picks_simulate, simulate_list, simulate_delete

app_name = "aiapp"

urlpatterns = [
    # メインダッシュボード
    path("", dashboard, name="dashboard"),
    path("toggle/", toggle_mode, name="toggle"),

    # AI Picks
    path("picks/", picks, name="picks"),

    # 非同期再生成 API
    path("api/picks/rebuild", picks_rebuild, name="picks_rebuild"),

    # JSON
    path("picks.json", picks_json, name="picks_json"),

    # AI設定
    path("settings/", settings_view, name="settings"),

    # シミュレ（登録）
    path("picks/simulate/", picks_simulate, name="picks_simulate"),

    # シミュレ一覧
    path("simulate/", simulate_list, name="simulate_list"),

    # シミュレ削除
    path("simulate/<int:pk>/delete/", simulate_delete, name="simulate_delete"),
]