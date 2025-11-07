"""
価格取得＋キャッシュ。M1はダミー返却。
"""
def get_prices(code, lookback=60):
    # TODO: 実装（無料ソース→キャッシュ保存）
    # いまは均等ステップのダミー終値
    base = 1000
    return [base + i*2 for i in range(lookback)]
