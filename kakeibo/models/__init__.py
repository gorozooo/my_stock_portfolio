# =========================================
# [FILE] __init__.py
# [PATH] kakeibo/models/__init__.py
#
# このファイルは何？
# modelsフォルダをDjangoに認識させ、
# AccountとTransactionを一括で読み込ませる。
# =========================================

from .account import Account
from .transaction import Transaction