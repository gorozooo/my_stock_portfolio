# advisor/ai/trainer.py（例）
# 実際のモデル名はあなたの repo に合わせて置換してください
from portfolio.models import Holding, RealizedTrade, Dividend, Cash  # TODO: 実名に合わせて
from collections import defaultdict

def build_feature_rows():
    rows = []
    # 例：実現損益と保有日数を学習データにまとめる（擬似）
    for t in RealizedTrade.objects.all()[:500]:
        rows.append({
            "ticker": t.ticker,
            "pnl_r": t.pnl_r,             # 例：R（リスク単位）利益
            "hold_days": t.hold_days,     # 例：保有日数
            "direction": t.side,          # "BUY"/"SELL"
            # TODO: SMA/出来高などの特徴量は後段で追加
        })
    return rows