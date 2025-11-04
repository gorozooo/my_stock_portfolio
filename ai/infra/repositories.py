from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple

# 既存アプリのモデルが無くてもクラッシュしないように防御的にimport
TrendResult = None
StockMaster = None
try:
    # 例：あなたの既存モデル名に合わせてここを後で修正
    from trend.models import TrendResult as _TrendResult  # 仮
    TrendResult = _TrendResult
except Exception:
    pass

try:
    from portfolio.models import StockMaster as _StockMaster  # 仮（銘柄名や業種）
    StockMaster = _StockMaster
except Exception:
    pass


def fetch_top_trend_candidates(limit: int = 30) -> List[Dict[str, Any]]:
    """
    候補のタタキ台（実データがあればそれを使い、無ければフォールバックを返す）
    返すDictのキー：
      code, name, sector, price, trend_d/w/m (up/flat/down), strength, vol_boost
    """
    # 実データの例：TrendResultから日足/週足/相対強度などで上位抽出
    if TrendResult is not None:
        try:
            qs = (TrendResult.objects
                  .select_related()
                  .order_by('-weekly_trend', '-confidence')[:limit])
            out: List[Dict[str, Any]] = []
            for r in qs:
                code = getattr(r, 'code', getattr(r, 'ticker', '0000'))
                name = getattr(r, 'name', f'銘柄{code}')
                sector = getattr(r, 'sector_jp', '不明')
                price = getattr(r, 'last_price', 2000.0)
                # 仮ルール：weekly_trend/ slope から方向をざっくり決める
                td = 'up' if getattr(r, 'daily_slope', 0) > 0 else ('down' if getattr(r, 'daily_slope', 0) < 0 else 'flat')
                tw = 'up' if getattr(r, 'weekly_trend', 0) > 0 else ('down' if getattr(r, 'weekly_trend', 0) < 0 else 'flat')
                tm = 'up' if getattr(r, 'monthly_trend', 0) > 0 else ('down' if getattr(r, 'monthly_trend', 0) < 0 else 'flat')
                strength = getattr(r, 'rs_n225', 0.0)  # 日経平均比
                volb = getattr(r, 'vol_spike', 1.0)    # 出来高倍率
                out.append(dict(
                    code=code, name=name, sector=sector, price=price,
                    trend_d=td, trend_w=tw, trend_m=tm,
                    strength=strength, vol_boost=volb,
                ))
            if out:
                return out
        except Exception:
            pass

    # フォールバック（データ無しでもUIが動く）
    demo = []
    samples = [
        ('7203', 'トヨタ自動車', '自動車・輸送機', 2450.0, 'up', 'up', 'flat', 1.2, 2.3),
        ('9432', '日本電信電話', '情報・通信',     188.4,  'flat','up',  'up',   0.8, 1.1),
        ('8035', '東京エレクトロン','電気機器',   38200.0, 'up', 'flat','up',   1.6, 1.8),
        ('6758', 'ソニーグループ', '電気機器',    12450.0, 'down','flat','flat',0.9, 1.3),
        ('9984', 'ソフトバンクG',  '情報・通信',   8750.0,  'up', 'up',  'up',   1.3, 2.0),
    ]
    for i in range(limit):
        c = samples[i % len(samples)]
        demo.append(dict(
            code=c[0], name=c[1], sector=c[2], price=c[3],
            trend_d=c[4], trend_w=c[5], trend_m=c[6],
            strength=c[7], vol_boost=c[8],
        ))
    return demo


def fetch_account_caps() -> Dict[str, Any]:
    """
    口座側の上限情報（現物買付可能額/NISA残/信用余力など）。
    今は固定値→ 後で cash/holdings から取得する。
    """
    return dict(
        cash_buyable=2_000_000,   # 現物買付可能額
        nisa_room=1_200_000,      # NISA残
        margin_power=3_000_000,   # 信用余力
    )