# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict
from datetime import date

from django.db.models import Max

from ..models_market import SectorSignal

def latest_sector_strength() -> Dict[str, dict]:
    """
    セクター名 -> {rs_score, advdec, vol_ratio, date} の辞書を返す
    最新日を自動特定（全セクターで同一最新日が無くても最大日で集約）
    """
    # 最新日
    max_date = SectorSignal.objects.aggregate(mx=Max("date")).get("mx")
    if not max_date:
        return {}

    out: Dict[str, dict] = {}
    for s in SectorSignal.objects.filter(date=max_date):
        out[s.sector] = dict(
            rs_score=float(s.rs_score or 0.0),
            advdec=float(s.advdec or 0.0),
            vol_ratio=float(s.vol_ratio or 1.0),
            date=max_date.isoformat(),
        )
    return out