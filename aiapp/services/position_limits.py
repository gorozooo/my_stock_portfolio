# -*- coding: utf-8 -*-
"""
aiapp.services.position_limits

同時ポジション制限（プロ仕様）
- max_positions: 最大同時保有数（例: 5）
- max_total_risk_r: 合計リスクR上限（例: 3.0）
- 1トレード=1R固定（現状の設計に合わせる）
- 候補は EV_true の降順で通す（高EVから枠を埋める）

このモジュールは
- ai_simulate_auto
- preview_simulate_level3
の両方から使う想定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class LimitConfig:
    max_positions: int = 5
    max_total_risk_r: float = 3.0


@dataclass
class SkipInfo:
    reason_code: str
    reason_msg: str
    open_count: int
    total_risk_r: float


class PositionLimitManager:
    """
    現在の“建玉状態”を外部から渡してもらい、
    新規建て可否を判定するだけの軽量クラス。

    ここでは「既に建ってるか」「何ポジションか」「合計リスクR」を管理する。
    """

    def __init__(self, cfg: LimitConfig):
        self.cfg = cfg
        self.open_positions: Dict[str, Dict[str, Any]] = {}
        self.total_risk_r: float = 0.0

    # -------- 状態ロード（外部からの注入） --------
    def load_open_positions(
        self,
        positions_by_code: Dict[str, Dict[str, Any]],
        *,
        total_risk_r: Optional[float] = None,
    ) -> None:
        self.open_positions = dict(positions_by_code or {})
        if total_risk_r is not None:
            self.total_risk_r = float(total_risk_r)
        else:
            # 各ポジの risk_r を足す（無ければ1.0扱い）
            s = 0.0
            for _code, p in self.open_positions.items():
                try:
                    s += float(p.get("risk_r", 1.0))
                except Exception:
                    s += 1.0
            self.total_risk_r = s

    # -------- 参照 --------
    def is_open(self, code: str) -> bool:
        return str(code) in self.open_positions

    def count_open(self) -> int:
        return len(self.open_positions)

    # -------- 判定 --------
    def can_open(self, code: str, *, risk_r: float = 1.0) -> Tuple[bool, Optional[SkipInfo]]:
        code = str(code)

        if self.is_open(code):
            return False, SkipInfo(
                reason_code="already_open",
                reason_msg="既に保有中のため（重複禁止）。",
                open_count=self.count_open(),
                total_risk_r=self.total_risk_r,
            )

        if self.count_open() >= int(self.cfg.max_positions):
            return False, SkipInfo(
                reason_code="max_positions",
                reason_msg=f"同時ポジション上限（{int(self.cfg.max_positions)}）に達したため。",
                open_count=self.count_open(),
                total_risk_r=self.total_risk_r,
            )

        try:
            r = float(risk_r)
        except Exception:
            r = 1.0

        if self.total_risk_r + r > float(self.cfg.max_total_risk_r):
            return False, SkipInfo(
                reason_code="max_total_risk",
                reason_msg=f"合計リスク上限（{float(self.cfg.max_total_risk_r):.2f}R）を超えるため。",
                open_count=self.count_open(),
                total_risk_r=self.total_risk_r,
            )

        return True, None

    # -------- 反映（建てた後に呼ぶ） --------
    def open(self, code: str, *, risk_r: float = 1.0, **payload: Any) -> None:
        code = str(code)
        if code in self.open_positions:
            return
        try:
            r = float(risk_r)
        except Exception:
            r = 1.0
        p = dict(payload or {})
        p["risk_r"] = r
        self.open_positions[code] = p
        self.total_risk_r += r

    def close(self, code: str) -> None:
        code = str(code)
        p = self.open_positions.pop(code, None)
        if not p:
            return
        try:
            r = float(p.get("risk_r", 1.0))
        except Exception:
            r = 1.0
        self.total_risk_r = max(0.0, self.total_risk_r - r)