# aiapp/models/__init__.py

# =========================================================
# 既存モデル
# =========================================================

from .master import StockMaster
from .vtrade import VirtualTrade

# =========================================================
# 指数・為替・レジーム系
# =========================================================

from .macro import (
    BenchmarkMaster,
    BenchmarkPrice,
    MacroRegimeSnapshot,
)


# =========================================================
# 行動統計（⭐️本番仕様）
# =========================================================

from .behavior_stats import BehaviorStats


# =========================================================
# public exports
# =========================================================

__all__ = [
    # --- core ---
    "StockMaster",
    "VirtualTrade",

    # --- macro ---
    "BenchmarkMaster",
    "BenchmarkPrice",
    "MacroRegimeSnapshot",

    # --- behavior / stars ---
    "BehaviorStats",
]