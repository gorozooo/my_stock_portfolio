"""
週1でJPXマスタ（銘柄・コード・33業種）を取得して保存。
M1では未実装：後で実データ取得に差し替え。
"""
from aiapp.models import StockMaster

def refresh_master():
    # TODO: 実装（無料ソース→CSV/JSON→DB反映）
    # いまはダミー1件挿入（重複時はスキップ）
    StockMaster.objects.get_or_create(
        code="6758",
        defaults={"name": "ソニーグループ", "sector33": "電機"},
    )
    return 1
