from __future__ import annotations
import os, glob, json, shutil, datetime as dt
from typing import Dict, Any, List
from django.conf import settings
import yaml

ACTIVE_DIR = os.path.join(settings.BASE_DIR, "advisor", "policies", "active")
SNAP_DIR   = os.path.join(settings.MEDIA_ROOT, "advisor", "policies")

REQUIRED_TOP = {"id","name","rules","targets","size","labels"}

class PolicyError(Exception): ...

def load_active_policies() -> List[Dict[str, Any]]:
    if not os.path.isdir(ACTIVE_DIR):
        return []
    pols: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(ACTIVE_DIR, "*.y*ml"))):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        miss = REQUIRED_TOP - set(data.keys())
        if miss:
            raise PolicyError(f"{os.path.basename(path)} 欠落: {','.join(sorted(miss))}")
        # デフォルト埋め
        data.setdefault("priority", 50)
        data.setdefault("limit", 20)
        # 型ざっくり
        data["rules"].setdefault("min_overall", 0)
        data["rules"].setdefault("min_theme", 0.0)
        data["rules"].setdefault("allow_weekly", ["up","flat","down"])
        data["rules"].setdefault("min_slope_yr", None)
        data["targets"].setdefault("tp_pct", 0.1)
        data["targets"].setdefault("sl_pct", 0.03)
        data["size"].setdefault("risk_pct", 0.01)
        pols.append(data)
    # priority降順
    pols.sort(key=lambda p: int(p.get("priority",0)), reverse=True)
    return pols

def snapshot_active_policies() -> str:
    """active配下を日付フォルダへコピーしてパスを返す"""
    today = dt.datetime.now().strftime("%Y%m%d")
    outdir = os.path.join(SNAP_DIR, today)
    os.makedirs(outdir, exist_ok=True)
    for p in glob.glob(os.path.join(ACTIVE_DIR, "*")):
        shutil.copy2(p, outdir)
    # 目録
    index = {"generated_at": dt.datetime.now().isoformat(), "files": []}
    for p in sorted(glob.glob(os.path.join(outdir, "*"))):
        index["files"].append(os.path.basename(p))
    with open(os.path.join(outdir, "_index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    return outdir