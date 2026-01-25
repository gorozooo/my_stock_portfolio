# aiapp/urls.py
from django.urls import path

from .views.dashboard import dashboard, toggle_mode
from .views.picks import picks, picks_json, picks_simulate
from .views.api import picks_rebuild  # AIピック再生成API
from .views.settings import settings_view  # AI設定画面
from .views.simulate import simulate_list  # シミュレ一覧
from .views.sim_delete import simulate_delete  # シミュレ削除
from .views.sim_result import simulate_result  # ★ シミュレ結果保存
from .views.behavior import behavior_dashboard
from .views.daytrade_backtest import daytrade_backtest_view

# ★ 追加：デバッグビュー
from .views import picks_debug

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

    # シミュレ登録（AI Picks のカードから）
    path("picks/simulate/", picks_simulate, name="ai_picks_simulate"),

    # シミュレ一覧
    path("simulate/", simulate_list, name="simulate_list"),

    # シミュレ削除
    path("simulate/<int:pk>/delete/", simulate_delete, name="simulate_delete"),

    # ★ シミュレ結果保存
    path("simulate/<int:pk>/result/", simulate_result, name="simulate_result"),
    
    # ★ シミュレ結果ダッシュボード
    path("behavior/", behavior_dashboard, name="behavior_dashboard"),

    # ========================================================
    # 📈 Daytrade Backtest（開発用UI）
    # 例:
    #   /ai/daytrade/backtest/
    # ========================================================
    path("daytrade/backtest/", daytrade_backtest_view, name="daytrade_backtest"),

    # ========================================================
    # 🔍 AI Picks デバッグ（最新JSONの中身を可視化）
    # 例:
    #   /ai/debug/picks/        → latest_full_all.json
    #   /ai/debug/picks/?kind=top → latest_full.json（TopK）
    # ========================================================
    path(
        "debug/picks/",
        picks_debug.picks_debug_view,
        name="picks_debug",
    ),
]