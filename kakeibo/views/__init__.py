# =========================================
# [FILE] __init__.py
# [PATH] kakeibo/views/__init__.py
#
# このファイルは何？
# viewsフォルダをDjangoに認識させ、
# 各View関数を外部からimport可能にする。
# =========================================

from .dashboard import dashboard
from .income import income
from .expense import expense, expense_fixed, expense_variable
from .bank import bank

# settings
from .settings import settings_view, settings_menu

# manage
from .manage import (
    manage_menu,
    manage_income,
    manage_variable,
    manage_fixed,
    manage_bank,
)