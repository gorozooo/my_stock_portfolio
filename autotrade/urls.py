# autotrade/urls.py
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

]