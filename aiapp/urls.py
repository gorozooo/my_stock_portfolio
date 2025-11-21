# aiapp/urls.py
from django.urls import path

from .views.dashboard import dashboard, toggle_mode
from .views.picks import picks, picks_json
from .views.api import picks_rebuild
from .views.settings import settings_view

# ★ シミュレ関連ビュー
from .views.simulate import picks_simulate, simulate_list
from .views.sim_delete import simulate_delete  # ← 追加：削除用

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

    # AI設定画面
    path("settings/", settings_view, name="settings"),

    # シミュレ登録（AI Picks の「シミュレ」ボタン用）
    path("picks/simulate/", picks_simulate, name="ai_picks_simulate"),

    # シミュレ一覧
    path("simulations/", simulate_list, name="simulate_list"),

    # ★ シミュレ削除（1件）
    path("simulations/<int:pk>/delete/", simulate_delete, name="simulate_delete"),
]