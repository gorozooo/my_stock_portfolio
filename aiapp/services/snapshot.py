# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Tuple, Optional
from django.conf import settings

PICKS_DIR = Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"

# 優先度：lite > full > latest(互換) > synthetic
CANDIDATE_BASENAMES = [
    "latest_lite.json",
    "latest_full.json",
    "latest.json",
    "latest_synthetic.json",
]

def _resolve(p: Path) -> Optional[Path]:
    """実体へ解決。壊れたシンボリックは None。"""
    try:
        if p.is_symlink():
            target = Path(os.readlink(p))
            if not target.is_absolute():
                target = (p.parent / target).resolve()
            return target if target.exists() else None
        return p if p.exists() else None
    except OSError:
        return None

def find_latest_snapshot() -> Tuple[Optional[Path], str]:
    if not PICKS_DIR.exists():
        return None, "missing"
    for name in CANDIDATE_BASENAMES:
        p = PICKS_DIR / name
        real = _resolve(p)
        if real is not None and real.exists():
            if name == "latest_lite.json": return real, "lite"
            if name == "latest_full.json": return real, "full"
            if name == "latest.json":      return real, "latest"
            if name == "latest_synthetic.json": return real, "synthetic"
    return None, "missing"

def load_snapshot() -> Tuple[Dict, str, Optional[Path]]:
    """
    戻り値: (data, kind, path)
      kind: lite|full|latest|synthetic|missing
      path: 実体パス or None
    """
    path, kind = find_latest_snapshot()
    if path is None:
        return {"meta": {"generated_at": None}, "items": []}, kind, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "items" not in data or not isinstance(data["items"], list):
            data = {"meta": {"generated_at": None}, "items": []}
        return data, kind, path
    except Exception:
        return {"meta": {"generated_at": None}, "items": []}, kind, path