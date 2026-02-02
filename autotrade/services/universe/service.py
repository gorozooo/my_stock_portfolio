"""
[FILE] autotrade/services/universe/service.py
[PATH] <project_root>/autotrade/services/universe/service.py

このファイルは何？
- 「今日の対象銘柄（5〜10）」を最終決定して、画面表示用の辞書（JSON）を返すサービスです。
- 朝ジョブ（morning_prepare）から呼ばれます。

このファイルの役割（初心者向け）
1) 候補（最大200）を読む
2) 日足メトリクス（売買代金・ATR%）を計算（キャッシュで高速化）
3) ranker.py で“事故りやすい銘柄”を落として、上位を作る（ここで説明文も作る）
4) 上位だけ朝指標（朝30分）を計算（重いので上位だけ）
5) 最終スコアで 5〜10 を返す

ポイント
- 「なぜこの銘柄なのか」が説明できるように、
  ranker.py が作った理由（why）を picks の reason_lines に保存します。
- UI側は reason_lines をそのまま箇条書きで表示すればOKになります。
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List

from django.conf import settings

from .universe_candidates_repo import load_candidates
from .metrics import compute_daily_metrics, DailyMetrics
from .ranker import filter_and_rank_daily, RankConfig
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
          "reason":"（1行要約）",
          "reason_lines":[...],
          "avg_dv_yen":..., "atr_pct":...,
          "score_daily":..., "score_liquidity":..., "score_atr":...,
          "morning_range_pct":..., "morning_eff":..., "morning_bars":...,
          "score_total":...
        },
        ...
      ]
    }
    """
    today = date.today()

    # =========
    # 0) 候補を読む（最大200）
    # =========
    candidates = load_candidates()
    candidates = candidates[:max(10, min(int(candidate_cap), len(candidates)))]

    if not candidates:
        return {
            "meta": {
                "date": today.isoformat(),
                "candidate_cap": int(candidate_cap),
                "note": "no_candidates",
            },
            "filter_stats": {
                "no_candidates": 1,
            },
            "picks": [],
        }

    # =========
    # 1) 日足メトリクス（売買代金・ATR%）を計算（キャッシュが効く）
    # =========
    metrics_map: Dict[str, DailyMetrics] = {}
    for t in candidates:
        metrics_map[t] = compute_daily_metrics(t, lookback_days=60, use_cache=True)

    # =========
    # 2) 日足でフィルタ＆ランキング（上位50）
    #    - ここで「説明可能な理由（why）」も作られる
    # =========
    cfg = RankConfig(
        min_avg_dv_yen=getattr(settings, "AUTOTRADE_MIN_AVG_DV_YEN", 300_000_000.0),
        min_price=getattr(settings, "AUTOTRADE_MIN_PRICE", 200.0),
        max_price=getattr(settings, "AUTOTRADE_MAX_PRICE", 20_000.0),
        min_atr_pct=getattr(settings, "AUTOTRADE_MIN_ATR_PCT", 0.008),
        max_atr_pct=getattr(settings, "AUTOTRADE_MAX_ATR_PCT", 0.060),
        w_liquidity=0.60,
        w_atr=0.40,
        atr_ideal_center_ratio=getattr(settings, "AUTOTRADE_ATR_IDEAL_CENTER_RATIO", 0.50),
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
                "candidate_cap": int(candidate_cap),
                "note": "no_candidates_after_filter",
            },
            "filter_stats": filter_stats,
            "picks": [],
        }

    # =========
    # 3) 朝指標（上位だけ）
    # =========
    rows: List[dict] = []
    for r in pre_ranked:
        t = r["ticker"]
        mm = compute_morning_metrics(t, today, use_cache=True)

        rows.append({
            **r,
            "morning_range_pct": mm.range_pct,
            "morning_eff": mm.efficiency,
            "morning_bars": mm.bars,
        })

    # =========
    # 4) 最終スコア（朝指標で軽く上澄みだけ調整）
    #    - 日足スコア(score_daily)が主役
    #    - 朝指標は“補助”として軽く加点・減点
    # =========
    def score_total(x: dict) -> float:
        s = float(x.get("score_daily", 0.0))

        rp = x.get("morning_range_pct")   # 朝の値幅（割合）
        ef = x.get("morning_eff")         # 朝の効率（0〜1目安）
        bars = int(x.get("morning_bars", 0) or 0)

        # 朝データが無いなら微減点（ただし落としきらない）
        if rp is None or ef is None or bars < 3:
            return s * 0.90

        bonus = 0.0

        # 朝レンジは “ある程度あると良い”
        # 0.2%未満: 弱い / 0.6%以上: しっかり / 2%以上: 荒い
        if rp >= 0.002:
            bonus += 0.05
        if rp >= 0.006:
            bonus += 0.05
        if rp >= 0.020:
            bonus -= 0.08

        # 効率は “両端を避ける”（往復しすぎ／一方向すぎ）
        if ef < 0.25:
            bonus -= 0.03
        elif ef > 0.85:
            bonus -= 0.03
        else:
            bonus += 0.03

        return float(s + bonus)

    for x in rows:
        x["score_total"] = float(score_total(x))

    rows.sort(key=lambda x: x["score_total"], reverse=True)

    # =========
    # 5) 最終 5〜10 銘柄
    # =========
    final_n = max(5, min(int(limit), 10))
    picks = rows[:final_n]

    out_picks: List[dict] = []
    for x in picks:
        t = x["ticker"]

        dv = x.get("avg_dv_yen")
        atr = x.get("atr_pct")

        # rankerが作った“理由文”をそのまま使う（画面が説明できるようになる）
        why_lines = x.get("why") if isinstance(x.get("why"), list) else []
        why_lines = [str(s) for s in why_lines if str(s).strip()]

        # 1行要約（初心者向け）
        if why_lines:
            reason_one = " / ".join(why_lines[:2])
        else:
            reason_one = "流動性（売買代金）と動き（ATR%）で選定"

        out_picks.append({
            "ticker": t,
            "reason": reason_one,
            "reason_lines": why_lines,

            # 日足メトリクス
            "avg_dv_yen": dv,
            "atr_pct": atr,
            "score_daily": x.get("score_daily"),
            "score_liquidity": x.get("score_liquidity"),
            "score_atr": x.get("score_atr"),

            # 朝指標
            "morning_range_pct": x.get("morning_range_pct"),
            "morning_eff": x.get("morning_eff"),
            "morning_bars": x.get("morning_bars"),

            # 最終スコア
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