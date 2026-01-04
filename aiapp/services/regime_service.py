# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Literal

Regime = Literal["risk_on_lowvol", "risk_on_highvol", "risk_off_lowvol", "risk_off_highvol"]

class RegimeService:
    """
    簡易レジーム認識（将来拡張ポイント）。
    ここでは暫定で常に 'risk_on_lowvol' を返す（本番で指数などに連動）。
    """
    def detect(self) -> Regime:
        # TODO: 日経/トピ/VI/為替を参照して判定
        return "risk_on_lowvol"