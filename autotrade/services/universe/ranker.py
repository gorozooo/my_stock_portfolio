"""
[FILE] autotrade/services/universe/ranker.py
[PATH] <project_root>/autotrade/services/universe/ranker.py

このファイルは何？
- 200候補を「日足メトリクス」でふるいにかけて、上位を返す部品です。
- ここでは 5分足を使いません（重いから）。まず日足だけで “安全に絞る”。

絞り込み（フィルタ）
- 売買代金が少ない → 約定しにくい → 除外
- ATR%が小さすぎ → 動かなすぎ → 除外
- ATR%が大きすぎ → 荒すぎ → 除外
- 価格が極端 → 必要に応じて除外

スコア
- 流動性（売買代金）を最重要（60%）
- ボラ（ATR%）を次点（40%）
- 朝指標は第2段階で使用（ここでは未使用）

初心者ポイント
- ここで “事故りやすい銘柄” を徹底排除します。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import math

# ★ 修正点：metrics.py に統一
from .metrics import DailyMetrics


@dataclass
class RankConfig:
    # フィルタ条件
    min_avg_dv_yen: float = 300_000_000.0     # 3億円/日 目安
    min_price: float = 200.0                 # 200円未満は避ける
    max_price: float = 20_000.0              # 2万円超は避ける
    min_atr_pct: float = 0.008               # 0.8% 未満は動かなすぎ
    max_atr_pct: float = 0.060               # 6% 超は荒すぎ

    # スコア重み（この段階は日足のみ）
    w_liquidity: float = 0.60
    w_atr: float = 0.40


def _pct_rank(values: List[float]) -> Dict[int, float]:
    """
    値の順位を 0〜1 に正規化（単純ランク）
    """
    xs = [(i, v) for i, v in enumerate(values)]
    xs.sort(key=lambda x: x[1])
    n = len(xs)
    if n <= 1:
        return {xs[0][0]: 1.0} if n == 1 else {}
    out = {}
    for r, (i, _v) in enumerate(xs):
        out[i] = r / (n - 1)
    return out


def filter_and_rank_daily(
    candidates: List[str],
    metrics_map: Dict[str, DailyMetrics],
    cfg: Optional[RankConfig] = None,
    top_n: int = 50,
):
    """
    戻り値：
    - ranked: [{ticker, score_daily, ...}] 上位 top_n
    - stats : フィルタ統計（何が何件落ちたか）
    """
    cfg = cfg or RankConfig()

    rows = []
    stats = {
        "no_data": 0,
        "dv_low": 0,
        "price_out": 0,
        "atr_out": 0,
        "ok": 0,
    }

    for t in candidates:
        m = metrics_map.get(t)
        if not m or m.last_close is None or m.avg_dv_yen is None or m.atr_pct is None:
            stats["no_data"] += 1
            continue

        price = float(m.last_close)
        dv = float(m.avg_dv_yen)
        atr = float(m.atr_pct)

        if dv < cfg.min_avg_dv_yen:
            stats["dv_low"] += 1
            continue

        if price < cfg.min_price or price > cfg.max_price:
            stats["price_out"] += 1
            continue

        if atr < cfg.min_atr_pct or atr > cfg.max_atr_pct:
            stats["atr_out"] += 1
            continue

        rows.append({
            "ticker": t,
            "price": price,
            "avg_dv_yen": dv,
            "atr_pct": atr,
        })
        stats["ok"] += 1

    if not rows:
        return [], stats

    # 正規化（ランク）
    dvs = [r["avg_dv_yen"] for r in rows]
    atrs = [r["atr_pct"] for r in rows]
    dv_rank = _pct_rank(dvs)
    atr_rank = _pct_rank(atrs)

    for i, r in enumerate(rows):
        s_liq = dv_rank.get(i, 0.0)
        s_atr = atr_rank.get(i, 0.0)
        r["score_daily"] = float(cfg.w_liquidity * s_liq + cfg.w_atr * s_atr)

    rows.sort(key=lambda x: x["score_daily"], reverse=True)
    ranked = rows[:max(1, int(top_n))]

    return ranked, stats