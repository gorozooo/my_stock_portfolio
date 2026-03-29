# [FILE] cash_service.py
# [PATH] portfolio/services/cash_service.py
#
# このファイルは何？
# - 既存コード互換のための窓口
# - 実体は services/cash/balance_service.py に寄せる

from .cash.balance_service import *  # noqa