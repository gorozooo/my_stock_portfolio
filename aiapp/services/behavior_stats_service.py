# aiapp/services/behavior_stats_service.py
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Dict, Any, List

from django.db import transaction
from django.utils import timezone

from aiapp.models.vtrade import VirtualTrade
from aiapp.models.behavior_stats import BehaviorStats


@dataclass
class StatsResult:
    code: str
    mode_period: str
    mode_aggr: str
    window_days: int

    trades: int
    wins: int
    losses: int
    flats: int

    win_rate: float
    avg_r: float

    score_0_1: float
    stars: int


def _score_to_stars(score_0_1: float) -> int:
    """
    0..1 を 1..5 に変換
    """
    s = float(score_0_1)
    if s < 0.35:
        return 1
    if s < 0.45:
        return 2
    if s < 0.55:
        return 3
    if s < 0.65:
        return 4
    return 5


def _calc_score_0_1(trades: int, wins: int, avg_r: float) -> float:
    """
    本番用のスコア(0..1)

    - 勝率はベイズ補正で少数サンプルの暴れを抑える
    - avg_r も混ぜる（tanhで圧縮）
    - trades が少ないときは信頼度ゲートで抑える
    """
    # ベイズ補正（弱めの事前分布）
    alpha = 2.0
    beta = 2.0
    bayes_win = (wins + alpha) / (trades + alpha + beta) if trades > 0 else 0.5

    # avg_r を 0..1 に圧縮（-∞..∞ → 0..1）
    r_scaled = (math.tanh(avg_r / 1.0) + 1.0) / 2.0

    # 合成（勝率重視＋Rも混ぜる）
    base = 0.65 * bayes_win + 0.35 * r_scaled

    # サンプル数ゲート
    gate = 1.0 - math.exp(-trades / 10.0)

    score = base * gate
    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    return float(score)


def calc_stats(
    code: str,
    mode_period: str,
    mode_aggr: str,
    window_days: int = 90,
) -> StatsResult:
    """
    銘柄×モード別の直近 window_days の stats を計算して返す（保存はしない）

    ✅ PRO一択：
      VirtualTrade.result_r_pro のみで勝敗判定・平均Rを計算する
    """
    now = timezone.now()
    since = now - timedelta(days=int(window_days))

    qs = (
        VirtualTrade.objects
        .filter(code=str(code), mode_period=mode_period, mode_aggr=mode_aggr)
        .filter(closed_at__isnull=False)
        .filter(opened_at__gte=since)
        .values("result_r_pro")
    )

    trades = 0
    wins = 0
    losses = 0
    flats = 0
    r_sum = 0.0

    for row in qs:
        r_val = row.get("result_r_pro")
        if r_val is None:
            continue
        try:
            r = float(r_val)
        except Exception:
            continue

        trades += 1
        r_sum += r

        if r > 0:
            wins += 1
        elif r < 0:
            losses += 1
        else:
            flats += 1

    win_rate = (wins / trades) if trades > 0 else 0.0
    avg_r = (r_sum / trades) if trades > 0 else 0.0

    score_0_1 = _calc_score_0_1(trades=trades, wins=wins, avg_r=avg_r)
    stars = _score_to_stars(score_0_1)

    return StatsResult(
        code=str(code),
        mode_period=mode_period,
        mode_aggr=mode_aggr,
        window_days=int(window_days),
        trades=trades,
        wins=wins,
        losses=losses,
        flats=flats,
        win_rate=float(win_rate),
        avg_r=float(avg_r),
        score_0_1=float(score_0_1),
        stars=int(stars),
    )


@transaction.atomic
def upsert_stats(res: StatsResult) -> BehaviorStats:
    """
    計算結果を DB に保存（upsert）
    """
    obj, _created = BehaviorStats.objects.update_or_create(
        code=res.code,
        mode_period=res.mode_period,
        mode_aggr=res.mode_aggr,
        defaults={
            "window_days": res.window_days,
            "trades": res.trades,
            "wins": res.wins,
            "losses": res.losses,
            "flats": res.flats,
            "win_rate": res.win_rate,
            "avg_r": res.avg_r,
            "score_0_1": res.score_0_1,
            "stars": res.stars,
            "computed_at": timezone.now(),
        },
    )
    return obj


def refresh_all(window_days: int = 90) -> Dict[str, Any]:
    """
    VirtualTrade に存在する (code, mode_period, mode_aggr) の組を全て集計して保存。
    戻り値は軽いサマリ。
    """
    keys = (
        VirtualTrade.objects
        .values("code", "mode_period", "mode_aggr")
        .distinct()
    )

    total = 0
    updated = 0

    for k in keys:
        code = str(k["code"])
        mp = str(k["mode_period"])
        ma = str(k["mode_aggr"])

        res = calc_stats(code=code, mode_period=mp, mode_aggr=ma, window_days=window_days)
        upsert_stats(res)

        total += 1
        updated += 1

    return {"total_keys": total, "updated": updated, "window_days": int(window_days)}