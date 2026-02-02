"""
[FILE] autotrade/services/universe/ranker.py
[PATH] <project_root>/autotrade/services/universe/ranker.py

このファイルは何？
- 候補（最大200）を「日足メトリクス」でふるいにかけて、上位だけを返す部品です。
- “なぜこの銘柄が上位なのか” を説明できるように、スコアを分解して返します。

ここで使う日足メトリクス（初心者向け）
- 売買代金（円/日）: 大きいほど「注文が通りやすい」
- ATR%（1日の動き）: 小さすぎ→動かない / 大きすぎ→荒い → “ちょうど良い”が最強

このファイルのゴール（超重要）
- 上位銘柄の行に「why（理由の文章）」を載せる
  → 画面にそのまま出せる（＝説明可能）

戻り値の形（service.pyが期待する形を壊さない）
- ranked: [{ticker, price, avg_dv_yen, atr_pct, score_daily, ...}] 上位 top_n
- stats : フィルタ統計（何が何件落ちたか）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import math

from .metrics import DailyMetrics


@dataclass
class RankConfig:
    # --- フィルタ（安全のための足切り） ---
    min_avg_dv_yen: float = 300_000_000.0  # 3億円/日 目安（調整可）
    min_price: float = 200.0              # 200円未満は避ける（調整可）
    max_price: float = 20_000.0           # 2万円超は避ける（調整可）
    min_atr_pct: float = 0.008            # 0.8% 未満は動かなすぎ
    max_atr_pct: float = 0.060            # 6% 超は荒すぎ（初期）

    # --- スコア重み（この段階は日足のみ） ---
    # 流動性（売買代金）を重視しつつ、ATR%は「ちょうど良さ」を評価する
    w_liquidity: float = 0.60
    w_atr: float = 0.40

    # ATR%の「理想の中心位置」
    # 例：min=0.8% max=6.0% なら中間(約3.4%)あたりが “ちょうど良い”
    atr_ideal_center_ratio: float = 0.50  # 0.0=下限寄り, 1.0=上限寄り（基本は0.5）


def _pct_rank(values: List[float]) -> Dict[int, float]:
    """
    値の順位を 0〜1 に正規化（単純ランク）
    - 0.0 が最小、1.0 が最大
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


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _fmt_yen_dv(dv_yen: float) -> str:
    """
    売買代金を初心者向けに見やすく（億円/日）
    """
    if dv_yen is None or not math.isfinite(dv_yen):
        return "不明"
    oku = dv_yen / 100_000_000.0
    return f"{oku:.1f}億円/日"


def _fmt_price(yen: float) -> str:
    if yen is None or not math.isfinite(yen):
        return "不明"
    return f"{yen:,.0f}円"


def _fmt_pct(x: float) -> str:
    if x is None or not math.isfinite(x):
        return "不明"
    return f"{(x * 100.0):.2f}%"


def _atr_preference_score(atr_pct: float, cfg: RankConfig) -> float:
    """
    ATR%は「大きいほど良い」ではなく「ちょうど良い」を評価する。

    仕組み（初心者向け）
    - 下限(min)〜上限(max)の間に “理想の中心” を置く
    - 中心に近いほどスコアが高い
    - 端（min付近 / max付近）に寄るほどスコアが下がる
    """
    lo = float(cfg.min_atr_pct)
    hi = float(cfg.max_atr_pct)
    if hi <= lo:
        return 0.0

    # 理想中心（min〜maxの間）
    center = lo + (hi - lo) * float(cfg.atr_ideal_center_ratio)

    # 距離を 0〜1 に
    half = (hi - lo) / 2.0
    if half <= 0:
        return 0.0

    d = abs(float(atr_pct) - center) / half  # centerなら0、端なら1程度
    s = 1.0 - d
    return _clip01(s)


def filter_and_rank_daily(
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

    rows: List[dict] = []
    stats = {
        "no_data": 0,
        "dv_low": 0,
        "price_out": 0,
        "atr_out": 0,
        "ok": 0,
    }

    # 1) フィルタ（事故りやすいものを落とす）
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

    # 2) スコア材料（正規化）
    dvs = [r["avg_dv_yen"] for r in rows]
    dv_rank = _pct_rank(dvs)  # 大きいほど良い（0..1）

    # 3) スコア計算（説明可能な内訳にする）
    for i, r in enumerate(rows):
        dv = float(r["avg_dv_yen"])
        atr = float(r["atr_pct"])

        score_liquidity = float(dv_rank.get(i, 0.0))  # 0..1
        score_atr = float(_atr_preference_score(atr, cfg))  # 0..1（ちょうど良さ）

        score_daily = float(cfg.w_liquidity * score_liquidity + cfg.w_atr * score_atr)

        # --- 初心者向けの理由（why）を作る ---
        why: List[str] = []

        # 売買代金（流動性）
        if score_liquidity >= 0.80:
            why.append(f"売買代金が大きく、注文が通りやすい（{_fmt_yen_dv(dv)}）")
        elif score_liquidity >= 0.55:
            why.append(f"売買代金は十分（{_fmt_yen_dv(dv)}）")
        else:
            why.append(f"売買代金は最低条件はクリア（{_fmt_yen_dv(dv)}）")

        # ATR%（動き）
        # 「高い/低い」ではなく「ちょうど良い/端に寄ってる」を説明
        atr_lo = float(cfg.min_atr_pct)
        atr_hi = float(cfg.max_atr_pct)
        center = atr_lo + (atr_hi - atr_lo) * float(cfg.atr_ideal_center_ratio)

        if abs(atr - center) <= (atr_hi - atr_lo) * 0.12:
            why.append(f"動きがちょうど良い（ATR%={_fmt_pct(atr)}）")
        elif atr < center:
            why.append(f"動きはやや小さめ（ATR%={_fmt_pct(atr)}）→ 速く伸びにくいかも")
        else:
            why.append(f"動きはやや大きめ（ATR%={_fmt_pct(atr)}）→ 荒くなる可能性")

        # 価格帯（初心者向けの安心材料）
        why.append(f"価格帯={_fmt_price(r['price'])}")

        r["score_liquidity"] = score_liquidity
        r["score_atr"] = score_atr
        r["score_daily"] = score_daily
        r["why"] = why

    rows.sort(key=lambda x: x["score_daily"], reverse=True)
    ranked = rows[:max(1, int(top_n))]

    return ranked, stats