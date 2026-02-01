"""
[FILE] autotrade/services/universe_service.py
[PATH] <project_root>/autotrade/services/universe/service.py

このファイルは何？
- 「今日の対象銘柄（5〜10）」を最終決定するサービスです。

第2弾でやること
1) 候補（最大200）に対して日足指標を計算 → フィルタ＆ランキング（上位50）
2) 上位50だけ朝30分の5分足を見て朝指標（レンジ%・効率）を計算
3) 最終的に5〜10銘柄を決定して返す

初心者ポイント
- いきなり200銘柄に5分足を取りに行かないのがコツ（制限と速度の問題があるため）
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from django.conf import settings

from .universe_candidates_repo import load_candidates
from .universe_metrics import compute_daily_metrics, DailyMetrics
from .universe_ranker import filter_and_rank_daily, RankConfig
from .morning_data_service import compute_morning_metrics


def build_daily_universe(limit: int = 10, candidate_cap: int = 200) -> Dict:
    """
    戻り値例:
    {
      "meta": {...},
      "filter_stats": {...},
      "picks":[
        {
          "ticker":"7203.T",
          "reason":"流動性/ボラ/朝指標",
          "avg_dv_yen":..., "atr_pct":...,
          "morning_range_pct":..., "morning_eff":...,
          "score_total":...
        },
        ...
      ]
    }
    """
    today = date.today()

    # 0) 候補（最大200）を読む
    candidates = load_candidates()
    candidates = candidates[:max(10, min(int(candidate_cap), len(candidates)))]

    # 1) 日足メトリクスを計算（キャッシュ効く）
    metrics_map: Dict[str, DailyMetrics] = {}
    for t in candidates:
        metrics_map[t] = compute_daily_metrics(t, lookback_days=60, use_cache=True)

    # 2) 日足でフィルタ＆ランキング（上位50）
    cfg = RankConfig(
        min_avg_dv_yen=getattr(settings, "AUTOTRADE_MIN_AVG_DV_YEN", 300_000_000.0),
        min_price=getattr(settings, "AUTOTRADE_MIN_PRICE", 200.0),
        max_price=getattr(settings, "AUTOTRADE_MAX_PRICE", 20_000.0),
        min_atr_pct=getattr(settings, "AUTOTRADE_MIN_ATR_PCT", 0.008),
        max_atr_pct=getattr(settings, "AUTOTRADE_MAX_ATR_PCT", 0.060),
        w_liquidity=0.60,
        w_atr=0.40,
    )
    pre_ranked, filter_stats = filter_and_rank_daily(
        candidates=candidates,
        metrics_map=metrics_map,
        cfg=cfg,
        top_n=int(getattr(settings, "AUTOTRADE_PRESELECT_TOPN", 50)),
    )

    if not pre_ranked:
        return {
            "meta": {
                "date": today.isoformat(),
                "candidate_cap": candidate_cap,
                "note": "no_candidates_after_filter",
            },
            "filter_stats": filter_stats,
            "picks": [],
        }

    # 3) 朝指標（上位50だけ）
    rows = []
    for r in pre_ranked:
        t = r["ticker"]
        mm = compute_morning_metrics(t, today, use_cache=True)

        # 朝指標が取れない時は、日足スコアだけで一旦残す（ただしスコアは控えめ）
        range_pct = mm.range_pct
        eff = mm.efficiency
        bars = mm.bars

        rows.append({
            **r,
            "morning_range_pct": range_pct,
            "morning_eff": eff,
            "morning_bars": bars,
        })

    # 4) 最終スコア（朝指標で上澄みだけ選ぶ）
    #    - ここでは “朝レンジがそれなり” かつ “効率が高すぎない/低すぎない” を軽く加点
    #    - 本命の戦略切替は strategy_decider に任せる（役割分担）
    def score_total(x):
        s = float(x.get("score_daily", 0.0))

        rp = x.get("morning_range_pct")
        ef = x.get("morning_eff")
        bars = int(x.get("morning_bars", 0))

        # 朝データが無いなら微減点（ただし落としきらない）
        if rp is None or ef is None or bars < 3:
            return s * 0.85

        # 朝レンジは “ある程度あると良い” → 0.2%未満は弱い、2%以上は荒い
        # ここは軽い補正（本命の判断は別）
        bonus = 0.0
        if rp >= 0.002:
            bonus += 0.06
        if rp >= 0.006:
            bonus += 0.06
        if rp >= 0.020:
            bonus -= 0.08

        # 効率は “極端に低い＝往復” “極端に高い＝一方向伸びすぎ” の両端を避ける
        if ef < 0.25:
            bonus -= 0.03
        elif ef > 0.85:
            bonus -= 0.03
        else:
            bonus += 0.03

        return s + bonus

    for x in rows:
        x["score_total"] = float(score_total(x))

    rows.sort(key=lambda x: x["score_total"], reverse=True)

    # 5) 最終5〜10銘柄
    final_n = max(5, min(int(limit), 10))
    picks = rows[:final_n]

    # 表示用 reason を付ける（初心者向け）
    out_picks = []
    for x in picks:
        t = x["ticker"]
        dv = x.get("avg_dv_yen")
        atr = x.get("atr_pct")
        rp = x.get("morning_range_pct")
        ef = x.get("morning_eff")

        reason = "流動性/ボラ/朝指標で選定"
        out_picks.append({
            "ticker": t,
            "reason": reason,
            "avg_dv_yen": dv,
            "atr_pct": atr,
            "morning_range_pct": rp,
            "morning_eff": ef,
            "score_total": x.get("score_total"),
        })

    return {
        "meta": {
            "date": today.isoformat(),
            "candidate_cap": int(candidate_cap),
            "preselect_topn": int(getattr(settings, "AUTOTRADE_PRESELECT_TOPN", 50)),
            "final_n": int(final_n),
        },
        "filter_stats": filter_stats,
        "picks": out_picks,
    }
