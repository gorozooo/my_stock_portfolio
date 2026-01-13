# aiapp/services/picks_build_hybrid/emit_service_hybrid.py
# -*- coding: utf-8 -*-
"""
HYBRID 用のJSON出力。

- latest_full_hybrid_all.json / latest_full_hybrid.json のように suffix 付きで出す
- meta/items の構造は従来と同じ（schemaが拡張されるだけ）
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List

from aiapp.services.picks_build.schema import PickItem
from aiapp.services.picks_build.settings import PICKS_DIR, dt_now_stamp


def emit_json_hybrid(
    all_items: List[PickItem],
    top_items: List[PickItem],
    *,
    mode: str,
    style: str,
    horizon: str,
    universe: str,
    topk: int,
    meta_extra: Dict[str, Any],
    out_suffix: str = "hybrid",
) -> None:
    meta: Dict[str, Any] = {
        "mode": mode,
        "style": style,
        "horizon": horizon,
        "universe": universe,
        "total": len(all_items),
        "topk": topk,
    }
    meta.update({k: v for k, v in (meta_extra or {}).items() if v is not None})

    data_all = {"meta": meta, "items": [asdict(x) for x in all_items]}
    data_top = {"meta": meta, "items": [asdict(x) for x in top_items]}

    PICKS_DIR.mkdir(parents=True, exist_ok=True)

    suf = (out_suffix or "hybrid").strip().lower()
    # 全件（検証用）
    out_all_latest = PICKS_DIR / f"latest_full_{suf}_all.json"
    out_all_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full_{suf}_all.json"
    out_all_latest.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    out_all_stamp.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # TopK（UI用）
    out_top_latest = PICKS_DIR / f"latest_full_{suf}.json"
    out_top_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full_{suf}.json"
    out_top_latest.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    out_top_stamp.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")