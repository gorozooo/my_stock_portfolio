"""
[FILE] autotrade/views.py
[PATH] <project_root>/autotrade/views.py

このファイルは何？
- autotrade の View の “入口（ハブ）” です。
- views_dashboard.py / views_tuning.py / views_api.py に分割した各Viewを再エクスポートします。
- urls.py からは常にここ（autotrade.views）を参照しても壊れないようにするためのハブです。

今回の変更：
- 廃止済みの tuning_rerun_snapshot_force を __all__ からも完全削除。
  （ImportError/混乱の温床になるので、残骸を消す）
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
    "tuning_rerun_snapshot_refresh_picks",
    "tuning_result",
    "tuning_make_candidate",
    "tuning_promote_active",
    "tuning_rollback_active",

    # api
    "api_emergency_stop",
]