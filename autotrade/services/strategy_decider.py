"""
[FILE] autotrade/services/strategy_decider.py
[PATH] <project_root>/autotrade/services/strategy_decider.py

このファイルは何？
- 9:30時点で “今日の戦略” を確定するサービスです。

第2弾（実データ判定・方式2）
- 朝30分（9:00〜9:30）の値動きから
  1) 朝レンジ%（動いたか）
  2) 効率（トレンドっぽいか）
を使い、過去分布に対する “相対位置” で判定します。

判定イメージ（初心者向け）
- よく動いていて、しかも一方向に伸びている → BREAKOUT
- 動きが小さい or 往復してる → VWAP

注意
- 過去データが無い場合はフォールバック（固定しきい値）で決めます。
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

from django.conf import settings

from autotrade.models import AutoTradeDailyState
from .morning_data_service import compute_morning_metrics


JST = "Asia/Tokyo"


@dataclass
class DecideConfig:
    history_days: int = 20  # 相対判定の履歴日数
    # フォールバック（固定しきい値）
    range_pct_breakout: float = 0.008   # 0.8%
    eff_breakout: float = 0.60
    # 相対判定の閾値（分位）
    q_range: float = 0.70  # 朝レンジが上位30%なら「大きい」
    q_eff: float = 0.65    # 効率が上位35%なら「トレンドっぽい」


def _hist_path() -> str:
    base = getattr(settings, "MEDIA_ROOT", "media")
    d = os.path.join(base, "autotrade", "cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "strategy_history.jsonl")


def _load_history() -> List[Dict]:
    path = _hist_path()
    if not os.path.exists(path):
        return []
    xs = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    xs.append(json.loads(s))
                except Exception:
                    continue
    except Exception:
        return []
    return xs


def _append_history(rec: Dict) -> None:
    path = _hist_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _percentile(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    # q in [0,1]
    pos = q * (len(ys) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ys) - 1)
    frac = pos - lo
    return ys[lo] * (1 - frac) + ys[hi] * frac


def _aggregate_today_metrics(state: AutoTradeDailyState) -> Tuple[Optional[float], Optional[float], str]:
    """
    今日の朝指標を “対象銘柄” から集約して返す。
    - range_pct: 中央値（median）
    - efficiency: 中央値
    """
    today = date.today()
    picks = []
    u = state.universe if isinstance(state.universe, dict) else {}
    for x in (u.get("picks") or []):
        t = x.get("ticker")
        if t:
            picks.append(t)

    if not picks:
        return None, None, "no_picks"

    ranges = []
    effs = []
    for t in picks:
        mm = compute_morning_metrics(t, today, use_cache=True)
        if mm.range_pct is not None and mm.efficiency is not None and mm.bars >= 3:
            ranges.append(float(mm.range_pct))
            effs.append(float(mm.efficiency))

    if not ranges or not effs:
        return None, None, "no_morning_data"

    ranges_sorted = sorted(ranges)
    effs_sorted = sorted(effs)

    def median(a: List[float]) -> float:
        n = len(a)
        if n % 2 == 1:
            return a[n // 2]
        return 0.5 * (a[n // 2 - 1] + a[n // 2])

    return median(ranges_sorted), median(effs_sorted), "ok"


def decide_strategy(state: AutoTradeDailyState) -> str:
    cfg = DecideConfig(
        history_days=int(getattr(settings, "AUTOTRADE_STRATEGY_HIST_DAYS", 20)),
        range_pct_breakout=float(getattr(settings, "AUTOTRADE_RANGE_PCT_BREAKOUT", 0.008)),
        eff_breakout=float(getattr(settings, "AUTOTRADE_EFF_BREAKOUT", 0.60)),
        q_range=float(getattr(settings, "AUTOTRADE_Q_RANGE", 0.70)),
        q_eff=float(getattr(settings, "AUTOTRADE_Q_EFF", 0.65)),
    )

    today = date.today()
    today_range, today_eff, note = _aggregate_today_metrics(state)

    # 履歴を読む（過去分布のため）
    hist = _load_history()
    # 直近 history_days だけ使う
    hist = [x for x in hist if "date" in x and "range_pct" in x and "eff" in x]
    hist = hist[-max(5, cfg.history_days):]

    # 相対判定に十分な履歴が無い場合はフォールバック
    if today_range is None or today_eff is None or len(hist) < 10:
        # 固定しきい値
        if (today_range is not None and today_eff is not None
                and today_range >= cfg.range_pct_breakout
                and today_eff >= cfg.eff_breakout):
            strategy = "BREAKOUT"
        else:
            strategy = "VWAP"

        # 今日分は履歴に追加（次から賢くなる）
        if today_range is not None and today_eff is not None:
            _append_history({
                "date": today.isoformat(),
                "range_pct": float(today_range),
                "eff": float(today_eff),
                "strategy": strategy,
                "note": f"fallback:{note}",
            })
        return strategy

    # 相対判定：過去分布の分位点と比較
    hist_ranges = [float(x["range_pct"]) for x in hist if x.get("range_pct") is not None]
    hist_effs = [float(x["eff"]) for x in hist if x.get("eff") is not None]

    thr_range = _percentile(hist_ranges, cfg.q_range)
    thr_eff = _percentile(hist_effs, cfg.q_eff)

    if thr_range is None or thr_eff is None:
        # これもフォールバック
        strategy = "VWAP"
    else:
        # “動いていて” かつ “トレンドっぽい” → BREAKOUT
        if today_range >= thr_range and today_eff >= thr_eff:
            strategy = "BREAKOUT"
        else:
            strategy = "VWAP"

    # 履歴に追加（翌日以降、基準が勝手に自動調整される）
    _append_history({
        "date": today.isoformat(),
        "range_pct": float(today_range),
        "eff": float(today_eff),
        "strategy": strategy,
        "thr_range": float(thr_range) if thr_range is not None else None,
        "thr_eff": float(thr_eff) if thr_eff is not None else None,
        "note": "relative",
    })

    return strategy