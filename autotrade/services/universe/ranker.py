"""
[FILE] autotrade/services/universe/ranker.py
[PATH] <project_root>/autotrade/services/universe/ranker.py

このファイルは何？
- 候補（最大200）を「日足メトリクス」でふるいにかけて、上位を返す部品です。
- ここでは 5分足は使いません（重いので）。まず日足だけで“事故りやすい銘柄”を落とします。

初心者向けの判断軸（この段階で見るのは2つだけ）
1) 売買代金（avg_dv_yen）
   - 大きいほど「約定しやすい」＝安全
2) ATR%（atr_pct）
   - 小さすぎ → 動かない（チャンス少）
   - 大きすぎ → 荒すぎ（事故りやすい）
   - つまり “ちょうど良いゾーン” を高得点にする

このファイルのポイント
- 「なぜこの銘柄が上なのか」を説明できるように
  各銘柄に why（理由の箇条書き）を付けます。
- UI側は why をそのまま表示すればOK。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import math

from .metrics import DailyMetrics


# =====================
# 設定（フィルタ＆重み）
# =====================
@dataclass
class RankConfig:
    # フィルタ（落とす基準）
    min_avg_dv_yen: float = 300_000_000.0     # 平均売買代金：3億円/日 目安
    min_price: float = 200.0                 # 200円未満は避ける
    max_price: float = 20_000.0              # 2万円超は避ける
    min_atr_pct: float = 0.008               # 0.8%未満は動かなすぎ
    max_atr_pct: float = 0.060               # 6%超は荒すぎ

    # スコア重み（日足のみ）
    w_liquidity: float = 0.60                # 流動性（約定しやすさ）
    w_atr: float = 0.40                      # ATRの“ちょうど良さ”

    # ATRの“理想中心”を min〜max のどこに置くか（0.0〜1.0）
    # 例：0.50なら真ん中、0.35なら少し穏やか寄り
    atr_ideal_center_ratio: float = 0.50


# =====================
# 表示補助（初心者向け）
# =====================
def _yen_short(x: float) -> str:
    """
    例：
    1200000000 -> "12.0億円"
    350000000  -> "3.5億円"
    """
    if x is None or not math.isfinite(float(x)):
        return "-"
    x = float(x)
    oku = x / 100_000_000.0
    if oku >= 1.0:
        return f"{oku:.1f}億円"
    man = x / 10_000.0
    if man >= 1.0:
        return f"{man:.0f}万円"
    return f"{x:.0f}円"


def _pct(x: float) -> str:
    if x is None or not math.isfinite(float(x)):
        return "-"
    return f"{float(x) * 100:.2f}%"


# =====================
# スコア用ユーティリティ
# =====================
def _rank_0_1(values: List[float]) -> List[float]:
    """
    値を 0〜1 に正規化（単純ランク）
    - 小さいほど0、大きいほど1
    """
    n = len(values)
    if n <= 1:
        return [1.0] * n
    idx = list(range(n))
    idx.sort(key=lambda i: values[i])
    out = [0.0] * n
    for r, i in enumerate(idx):
        out[i] = r / (n - 1)
    return out


def _atr_ideal_score(atr: float, cfg: RankConfig) -> float:
    """
    ATR%を「ちょうど良いほど高得点」にする。

    - min〜max の範囲内にいることは filter で保証済み
    - その上で “理想中心” に近いほど良い（山型）
    """
    lo = float(cfg.min_atr_pct)
    hi = float(cfg.max_atr_pct)
    if hi <= lo:
        return 0.0

    center = lo + (hi - lo) * float(cfg.atr_ideal_center_ratio)
    half = (hi - lo) / 2.0

    # center からの距離（0が理想）
    dist = abs(float(atr) - center)

    # 距離が half を超えると0点、中心で1点
    score = 1.0 - (dist / max(half, 1e-9))
    return float(max(0.0, min(1.0, score)))


# =====================
# メイン：フィルタ＆ランキング
# =====================
def filter_and_rank_daily(
    *,
    candidates: List[str],
    metrics_map: Dict[str, DailyMetrics],
    cfg: Optional[RankConfig] = None,
    top_n: int = 50,
) -> Tuple[List[dict], dict]:
    """
    戻り値：
    - ranked: [{ticker, score_daily, score_liquidity, score_atr, why, ...}] 上位 top_n
    - stats : フィルタ統計（何が何件落ちたか）
    """
    cfg = cfg or RankConfig()

    stats = {
        "no_data": 0,
        "dv_low": 0,
        "price_out": 0,
        "atr_out": 0,
        "ok": 0,
    }

    rows: List[dict] = []

    # 1) まずフィルタで落とす（事故りやすいのはここで除外）
    for t in candidates:
        m = metrics_map.get(t)
        if not m:
            stats["no_data"] += 1
            continue

        # 必須
        if m.last_close is None or m.avg_dv_yen is None or m.atr_pct is None:
            stats["no_data"] += 1
            continue

        price = float(m.last_close)
        dv = float(m.avg_dv_yen)
        atr = float(m.atr_pct)

        # 売買代金が薄い → 約定しづらい
        if dv < float(cfg.min_avg_dv_yen):
            stats["dv_low"] += 1
            continue

        # 値段が極端 → 取り扱いが難しくなりやすい
        if price < float(cfg.min_price) or price > float(cfg.max_price):
            stats["price_out"] += 1
            continue

        # ATR%がゾーン外 → 動かなすぎ or 荒すぎ
        if atr < float(cfg.min_atr_pct) or atr > float(cfg.max_atr_pct):
            stats["atr_out"] += 1
            continue

        stats["ok"] += 1
        rows.append({
            "ticker": t,
            "price": price,
            "avg_dv_yen": dv,
            "atr_pct": atr,
        })

    if not rows:
        return [], stats

    # 2) スコア化（分解して作る）
    dvs = [r["avg_dv_yen"] for r in rows]
    dv_rank = _rank_0_1(dvs)  # 売買代金：大きいほど良い

    for i, r in enumerate(rows):
        # スコア要素①：流動性（約定しやすさ）
        score_liq = float(dv_rank[i])

        # スコア要素②：ATRの“ちょうど良さ”
        score_atr = float(_atr_ideal_score(float(r["atr_pct"]), cfg))

        # 合成（日足スコア）
        score_daily = float(cfg.w_liquidity * score_liq + cfg.w_atr * score_atr)

        # 3) 理由（why）を作る：UIにそのまま出せる日本語
        why: List[str] = []

        # 売買代金の説明
        dv_yen = float(r["avg_dv_yen"])
        why.append(f"売買代金が十分（約定しやすい）：平均 {_yen_short(dv_yen)}/日")

        # ATR%の説明（専門語を避ける）
        atr_pct = float(r["atr_pct"])
        why.append(f"値動きが適度（動くけど荒すぎない）：ATR {_pct(atr_pct)}")

        # スコアの見える化（内部値だけど初心者にも見せてOK）
        # ※ UIで小さく出す用
        why.append(f"内訳：流動性 {score_liq:.2f} / 動きやすさ {score_atr:.2f}")

        r["score_liquidity"] = score_liq
        r["score_atr"] = score_atr
        r["score_daily"] = score_daily
        r["why"] = why

    # 4) 並べ替え → 上位
    rows.sort(key=lambda x: x["score_daily"], reverse=True)
    ranked = rows[:max(1, int(top_n))]

    return ranked, stats