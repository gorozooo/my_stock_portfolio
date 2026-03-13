# =========================================================
# [FILE] autotrade/urls.py
# [PATH] <project_root>/autotrade/urls.py
#
# このファイルは何？
# - autotrade アプリのURLルーティング（URLとView関数の紐付け）です。
#
# 今回の変更：
# - 専用結果ページ execution_report を維持
# - 日別のデモ成績履歴ページ demo_daily_history を追加
# - mode切替API api_set_execution_mode を維持
# - 不要な2ボタンは撤去し、「rerun-refresh-picks」のみに統一
# =========================================================

from django.urls import path
from . import views

app_name = "autotrade"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("report/", views.execution_report, name="execution_report"),
    path("history/demo-daily/", views.demo_daily_history, name="demo_daily_history"),

    # --- API ---
    path("api/emergency-stop/", views.api_emergency_stop, name="api_emergency_stop"),
    path("api/set-execution-mode/", views.api_set_execution_mode, name="api_set_execution_mode"),

    # --- Tuning ---
    path("lab/", views.tuning_list, name="tuning_list"),
    path("lab/new/", views.tuning_new, name="tuning_new"),
    path("lab/<int:pk>/", views.tuning_edit, name="tuning_edit"),
    path("lab/<int:pk>/archive/", views.tuning_archive, name="tuning_archive"),

    # 検証実行（BACKTEST）
    path("lab/<int:pk>/backtest/", views.tuning_run_backtest, name="tuning_run_backtest"),

    # 検証結果（カード表示）
    path("lab/result/<int:snapshot_id>/", views.tuning_result, name="tuning_result"),

    # 残す：結果ページから再検証（picks更新）
    path(
        "lab/result/<int:snapshot_id>/rerun-refresh-picks/",
        views.tuning_rerun_snapshot_refresh_picks,
        name="tuning_rerun_snapshot_refresh_picks",
    ),

    # F：CANDIDATE/ACTIVE/ROLLBACK
    path("lab/result/<int:snapshot_id>/candidate/", views.tuning_make_candidate, name="tuning_make_candidate"),
    path("lab/result/<int:snapshot_id>/promote/", views.tuning_promote_active, name="tuning_promote_active"),
    path("lab/result/<int:snapshot_id>/rollback/", views.tuning_rollback_active, name="tuning_rollback_active"),
]