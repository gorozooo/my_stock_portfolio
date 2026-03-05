# =========================================================
# [FILE] autotrade/urls.py
# [PATH] <project_root>/autotrade/urls.py
#
# このファイルは何？
# - autotrade アプリのURLルーティング（URLとView関数の紐付け）です。
#
# 安全装置（プロ運用品質）
# - 存在しないViewを参照すると、Djangoの起動チェックで落ちて cron も止まります。
# - よって、参照するViewは「views.pyのハブから再エクスポートされているものだけ」に限定します。
# - 不要な2ボタンは撤去し、「rerun-refresh-picks」のみに統一します。
# =========================================================

from django.urls import path
from . import views

app_name = "autotrade"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    # --- API ---
    path("api/emergency-stop/", views.api_emergency_stop, name="api_emergency_stop"),

    # --- Tuning ---
    path("lab/", views.tuning_list, name="tuning_list"),
    path("lab/new/", views.tuning_new, name="tuning_new"),
    path("lab/<int:pk>/", views.tuning_edit, name="tuning_edit"),
    path("lab/<int:pk>/archive/", views.tuning_archive, name="tuning_archive"),

    # 検証実行（BACKTEST）
    path("lab/<int:pk>/backtest/", views.tuning_run_backtest, name="tuning_run_backtest"),

    # 検証結果（カード表示）
    path("lab/result/<int:snapshot_id>/", views.tuning_result, name="tuning_result"),

    # ★ 残す：結果ページから再検証（picks更新）
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