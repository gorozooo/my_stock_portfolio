# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import date
from typing import Dict, Optional, Tuple, List

from django.db.models import Max

from ..models_market import SectorSignal

def latest_sector_rs_map(target: Optional[date] = None) -> Dict[str, Dict[str, float]]:
    """
    直近(または指定日)のセクター→{rs, chg5, chg20, vol_ratio} を返す。
    見つからないセクターは含まれない。
    """
    if target:
        qs = SectorSignal.objects.filter(date=target)
    else:
        # セクター毎の最新日付を拾って結合
        latest = (SectorSignal.objects
                  .values("sector")
                  .annotate(d=Max("date")))
        pairs = [(x["sector"], x["d"]) for x in latest]
        rs = {}
        for sec, d in pairs:
            row = SectorSignal.objects.filter(sector=sec, date=d).first()
            if row:
                rs[sec] = dict(
                    rs=float(row.rs_score),
                    chg5=float(row.meta.get("chg5", 0.0) if row.meta else 0.0),
                    chg20=float(row.meta.get("chg20", 0.0) if row.meta else 0.0),
                    vol_ratio=(None if row.vol_ratio is None else float(row.vol_ratio)),
                    date=str(row.date),
                )
        return rs

    # target日指定時
    out = {}
    for row in qs:
        out[row.sector] = dict(
            rs=float(row.rs_score),
            chg5=float(row.meta.get("chg5", 0.0) if row.meta else 0.0),
            chg20=float(row.meta.get("chg20", 0.0) if row.meta else 0.0),
            vol_ratio=(None if row.vol_ratio is None else float(row.vol_ratio)),
            date=str(row.date),
        )
    return out


def weighted_portfolio_rs(sectors: List[Dict]) -> float:
    """
    画面用 sectors（[{sector, mv, share_pct, ...}...]）と最新RSから
    ポート全体の加重RS（-1..+1）を算出。RSが無いセクターは0扱い。
    """
    rs_map = latest_sector_rs_map()
    total_mv = sum(max(0.0, float(s.get("mv") or 0.0)) for s in sectors) or 1.0
    acc = 0.0
    for s in sectors:
        sec = (s.get("sector") or "").strip()
        mv = max(0.0, float(s.get("mv") or 0.0))
        rs = float((rs_map.get(sec) or {}).get("rs", 0.0))
        acc += rs * (mv / total_mv)
    return float(acc)
    
def latest_sector_strength():
    """
    返り値の例:
    {
      "情報・通信": {"rs_score": 0.35, "date": "2025-01-01"},
      "素材":       {"rs_score": -0.10, "date": "2025-01-01"},
      ...
    }
    今はダミー（空）で返す。後で実データに差し替え。
    """
    return {}