# =========================================================
# [FILE] __init__.py
# [PATH] <project_root>/tradeai/services/demo/__init__.py
#
# このファイルは何？
# - tradeai のデモ売買サービス群の入口です。
# =========================================================

from .auto_entry_service import auto_open_demo_trades_for_user
from .auto_exit_service import auto_close_demo_trades_for_user

__all__ = [
    "auto_open_demo_trades_for_user",
    "auto_close_demo_trades_for_user",
]