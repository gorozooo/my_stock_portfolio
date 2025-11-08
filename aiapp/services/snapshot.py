# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Tuple, Optional

from django.conf import settings

PICKS_DIR = Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"

# 検索優先度：軽量 > フル > 旧latest > 合成
CANDIDATE_BASENAMES = [
    "latest_lite.json",
    "latest_full.json",
    "latest.json",              # 互換（あれば使う）
    "latest_synthetic.json",
]


def _resolve(p: Path) -> Optional[Path]:
    """実体へ解決。壊れたシンボリックは None。"""
    try:
        if p.is_symlink():
            target = Path(os.readlink(p))
            # 相対リンクにも対応
            if not target.is_absolute():
                target = (p.parent / target).resolve()
            return target if target.exists() else None
        return p if p.exists() else None
    except OSError:
        return None


def find_latest_snapshot() -> Tuple[Optional[Path], str]:
    """
    優先順にスナップショットを探す。
    戻り値: (実体Path or None, "lite|full|latest|synthetic|missing")
    """
    if not PICKS_DIR.exists():
        return None, "missing"

    for name in CANDIDATE_BASENAMES:
        p = PICKS_DIR / name
        real = _resolve(p)
        if real is not None and real.exists():
            if name == "latest_lite.json":
                return real, "lite"
            if name == "latest_full.json":
                return real, "full"
            if name == "latest.json":
                return real, "latest"
            if name == "latest_synthetic.json":
                return real, "synthetic"

    return None, "missing"


def load_snapshot() -> Tuple[Dict, str, Optional[Path]]:
    """
    スナップショットを読み込んで dict を返す。
    戻り値: (data, kind, path)
    kind: "lite|full|latest|synthetic|missing"
    path: 実体パス（見つからない場合 None）
    """
    path, kind = find_latest_snapshot()
    if path is None:
        return {"meta": {"generated_at": None}, "items": []}, kind, None

    try:
        txt = path.read_text(encoding="utf-8")
        data = json.loads(txt)
        # 最低限の形式保証
        if "items" not in data or not isinstance(data["items"], list):
            data = {"meta": {"generated_at": None}, "items": []}
        return data, kind, path
    except Exception:
        # 壊れたJSONは空として返す
        return {"meta": {"generated_at": None}, "items": []}, kind, path