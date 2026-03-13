# =========================================================
# [FILE] autotrade/views.py
# [PATH] <project_root>/autotrade/views.py
#
# このファイルは何？
# - autotrade の View の “入口（ハブ）” です。
# - views_dashboard.py / views_tuning.py / views_api.py に分割した各Viewを再エクスポートします。
# - urls.py からは常にここ（autotrade.views）を参照しても壊れないようにするためのハブです。
#
# 今回の修正：
# - execution_report を export 維持
# - demo_daily_history を export 追加
# - api_set_execution_mode を export 維持
# - urls.py から参照される View をここで必ず公開する
# =========================================================

from __future__ import annotations

# Dashboard / Report / History
from .views_dashboard import (
    dashboard,
    execution_report,
    demo_daily_history,
)

# Tuning（実験室）
from .views_tuning import (
    tuning_list,
    tuning_new,
    tuning_edit,
    tuning_archive,
    tuning_run_backtest,
    tuning_rerun_snapshot_refresh_picks,
    tuning_result,
    tuning_make_candidate,
    tuning_promote_active,
    tuning_rollback_active,
)

# API
from .views_api import (
    api_emergency_stop,
    api_set_execution_mode,
)

__all__ = [
    # dashboard / report / history
    "dashboard",
    "execution_report",
    "demo_daily_history",

    # tuning
    "tuning_list",
    "tuning_new",
    "tuning_edit",
    "tuning_archive",
    "tuning_run_backtest",
    "tuning_rerun_snapshot_refresh_picks",
    "tuning_result",
    "tuning_make_candidate",
    "tuning_promote_active",
    "tuning_rollback_active",

    # api
    "api_emergency_stop",
    "api_set_execution_mode",
]