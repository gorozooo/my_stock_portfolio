# -*- coding: utf-8 -*-
from __future__ import annotations

from django.db import models


class StockMaster(models.Model):
    """
    JPX銘柄マスタ（最小限）
    - code: 4桁など（ETF/REIT等も含む想定）
    - name: 企業名/銘柄名（NFKC正規化は取り込み側で実施）
    - sector_code: 33業種コード（文字列で保持、"50" など）
    - sector_name: 33業種の日本語名（例: 食料品）
    """
    code = models.CharField(max_length=12, unique=True, db_index=True)
    name = models.CharField(max_length=255)

    sector_code = models.CharField(max_length=16, null=True, blank=True)
    sector_name = models.CharField(max_length=255, null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        db_table = "aiapp_stock_master"

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code} {self.name}"