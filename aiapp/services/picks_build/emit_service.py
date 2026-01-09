# -*- coding: utf-8 -*-
"""
JSON 出力サービス。

- latest_full_all.json / latest_full.json と timestamp 付きファイルを出す
- meta/items の構造は従来と同じ
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List

from .schema import PickItem
from .settings import PICKS_DIR, dt_now_stamp


def emit_json(
    all_items: List[PickItem],
    top_items: List[PickItem],
    *,
    mode: str,
    style: str,
    horizon: str,
    universe: str,
    topk: int,
    meta_extra: Dict[str, Any],
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

    out_all_latest = PICKS_DIR / "latest_full_all.json"
    out_all_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full_all.json"
    out_all_latest.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))
    out_all_stamp.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))

    out_top_latest = PICKS_DIR / "latest_full.json"
    out_top_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full.json"
    out_top_latest.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))
    out_top_stamp.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))