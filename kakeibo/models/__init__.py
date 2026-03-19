# =========================================
# [FILE] __init__.py
# [PATH] kakeibo/models/__init__.py
#
# このファイルは何？
# modelsフォルダをDjangoに認識させ、
# 家計簿で使うモデルを一括importできるようにする。
#
# 今回の修正：
# - ダッシュボード下部の「月ごとの自由メモ」用モデル
#   MonthlyDashboardMemo を追加でimportする
# =========================================

from .account import Account
from .category import Category

from .monthly_income import MonthlyIncome
from .fixed_expense_template import FixedExpenseTemplate
from .monthly_variable_expense import MonthlyVariableExpense
from .bank_balance import BankBalance
from .monthly_snapshot import MonthlySnapshot
from .monthly_dashboard_memo import MonthlyDashboardMemo