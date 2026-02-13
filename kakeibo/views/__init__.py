# =========================================
# [FILE] __init__.py
# [PATH] kakeibo/views/__init__.py
#
# このファイルは何？
# viewsフォルダをDjangoに認識させ、
# 各View関数を外部からimport可能にする。
# =========================================

from .dashboard import dashboard
from .expense import expense_create
from .income import income_create
from .settings import settings_view, account_delete