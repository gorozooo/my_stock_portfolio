# aiapp/models/__init__.py

# 既存モデル
from .master import StockMaster
from .vtrade import VirtualTrade

# 新しく追加した “指数・為替・レジーム” モデル
from .macro import (
    BenchmarkMaster,
    BenchmarkPrice,
    MacroRegimeSnapshot,
)

__all__ = [
    # 既存
    "StockMaster",
    "VirtualTrade",

    # 新モデル
    "BenchmarkMaster",
    "BenchmarkPrice",
    "MacroRegimeSnapshot",
]