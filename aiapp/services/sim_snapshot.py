# aiapp/services/sim_snapshot.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Optional


SNAPSHOT_VERSION = 1
ENGINE_VERSION = "L3-2025-11"  # レベル3評価エンジンのバージョン名（お好みで）


@dataclass
class SimSnapshot:
    # メタ情報
    version: int
    engine_version: str
    created_at: str          # ISO文字列（保存時刻＝シミュレ登録時）
    user_id: int

    # 銘柄・モード
    code: str
    name: str
    side: str                # "buy" / "sell" （今は "buy" 固定でOK）
    mode: str                # "live" / "demo"

    # 注文条件
    entry_price: float       # 指値（AIが出したエントリー価格）
    tp_price: Optional[float]  # 利確指値（なければ None）
    sl_price: Optional[float]  # 損切指値（なければ None）
    qty_rakuten: float       # 楽天の数量（0なら注文なし）
    qty_matsui: float        # 松井の数量（0なら注文なし）

    # 評価ルール
    horizon_days: int        # 何営業日追うか
    time_in_force: str       # "DAY" / "GTC" など（当日限り 等）
    placed_at: str           # 実際の注文時刻（ts）

    # 将来拡張用（余白）
    extra: Dict[str, Any]    # 追加情報（ルールの名前 等）


def build_sim_snapshot(
    *,
    user_id: int,
    ts: datetime,
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
    """
    シミュレ1件ぶんの「注文スナップショット」を dict で作る。

    - ts         : シミュレ登録時刻（timezone aware の datetime 推奨）
    - entry/tp/sl: 画面に出している価格そのまま
    """
    if extra is None:
        extra = {}

    created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    placed_at = ts.isoformat()

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