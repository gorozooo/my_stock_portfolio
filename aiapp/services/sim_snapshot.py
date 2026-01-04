# aiapp/services/sim_snapshot.py
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from django.conf import settings


SNAPSHOT_VERSION = 1
ENGINE_VERSION = "L3-2025-11"  # レベル3評価エンジン側と揃える用


@dataclass
class SimSnapshot:
    # メタ
    version: int
    engine_version: str
    created_at: str      # 保存タイムスタンプ（UTC）
    user_id: int

    # 銘柄・モード
    code: str
    name: str
    side: str            # "buy" / "sell"
    mode: str            # "live" / "demo"

    # 注文条件（AIが出した時点の“世界線”）
    entry_price: float
    tp_price: Optional[float]
    sl_price: Optional[float]
    qty_rakuten: float
    qty_matsui: float

    # 評価ルール
    horizon_days: int    # 5営業日など
    time_in_force: str   # "DAY" など
    placed_at: str       # シミュレ登録時刻（ローカルISO）

    # 余白
    extra: Dict[str, Any]


def build_snapshot(
    *,
    user_id: int,
    ts_local: datetime,
    code: str,
    name: str,
    side: str,
    mode: str,
    entry_price: float,
    tp_price: Optional[float],
    sl_price: Optional[float],
    qty_rakuten: float,
    qty_matsui: float,
    horizon_days: int,
    time_in_force: str = "DAY",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """シミュレ1件分のスナップショット dict を作成"""
    if extra is None:
        extra = {}

    created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    placed_at = ts_local.isoformat()

    snap = SimSnapshot(
        version=SNAPSHOT_VERSION,
        engine_version=ENGINE_VERSION,
        created_at=created_at,
        user_id=int(user_id),
        code=str(code),
        name=str(name),
        side=side,
        mode=mode,
        entry_price=float(entry_price),
        tp_price=float(tp_price) if tp_price is not None else None,
        sl_price=float(sl_price) if sl_price is not None else None,
        qty_rakuten=float(qty_rakuten),
        qty_matsui=float(qty_matsui),
        horizon_days=int(horizon_days),
        time_in_force=time_in_force,
        placed_at=placed_at,
        extra=extra,
    )
    return asdict(snap)


def get_snapshot_base_dir(user_id: int) -> Path:
    """ユーザー別スナップショット保存ディレクトリ"""
    base = Path(settings.MEDIA_ROOT) / "aiapp" / "simulate_snapshots" / f"u{user_id}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_snapshot(user_id: int, snapshot: Dict[str, Any]) -> str:
    """
    スナップショットを JSON として保存し、ファイルパス(str) を返す。
    """
    base = get_snapshot_base_dir(user_id)
    code = snapshot.get("code", "unknown")
    placed_at = snapshot.get("placed_at", "").replace(":", "").replace("-", "")
    if not placed_at:
        placed_at = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    filename = f"{code}_{placed_at}.json"
    path = base / filename

    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def load_snapshot(path_str: str) -> Optional[Dict[str, Any]]:
    """
    保存済みスナップショット JSON を読み込む。
    """
    if not path_str:
        return None
    path = Path(path_str)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return None