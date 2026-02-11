"""
[FILE] autotrade/views.py
[PATH] <project_root>/autotrade/views.py

このファイルは何？
- autotrade の View の “入口（ハブ）” です。
- 以前は全部ここに書いていたため巨大化していました。
- 今回、内容を views_dashboard.py / views_tuning.py / views_api.py に分割し、
  それでも urls.py 側が壊れないように、このファイルから各Viewを再エクスポートします。
"""

from __future__ import annotations

# Dashboard
from .views_dashboard import (
    dashboard,
)

# Tuning（実験室）
from .views_tuning import (
    tuning_list,
    tuning_new,
    tuning_edit,
    tuning_archive,
    tuning_run_backtest,
    tuning_rerun_snapshot_force,
    tuning_rerun_snapshot_refresh_picks,
    tuning_result,
    tuning_make_candidate,
    tuning_promote_active,
    tuning_rollback_active,
)

# API
from .views_api import (
    api_emergency_stop,
)

__all__ = [
    # dashboard
    "dashboard",

    # tuning
    "tuning_list",
    "tuning_new",
    "tuning_edit",
    "tuning_archive",
    "tuning_run_backtest",
    "tuning_rerun_snapshot_force",
    "tuning_rerun_snapshot_refresh_picks",
    "tuning_result",
    "tuning_make_candidate",
    "tuning_promote_active",
    "tuning_rollback_active",

    # api
    "api_emergency_stop",
]