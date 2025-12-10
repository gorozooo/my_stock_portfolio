# -*- coding: utf-8 -*-
"""
aiapp.models.macro

指数・為替・先物などのベンチマークと、
それらから算出した「相場レジーム」を保持するモデル群。

- BenchmarkMaster
    → 日経・TOPIX・S&P500・NASDAQ・ドル円・VIX…など、
      ベンチマークのマスタ（種別・ティッカーなど）

- BenchmarkPrice
    → 各ベンチマークの終値ベースの日足OHLCV

- MacroRegimeSnapshot
    → 日付ごとの「相場環境まとめ」
       （日本株トレンド、米国株トレンド、為替、ボラ、総合レジームなど）
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class BenchmarkMaster(models.Model):
    """
    ベンチマークマスタ
    例:
      - code: "NK225", name: "日経平均",    kind: "INDEX_JP", symbol: "^N225"
      - code: "TOPIX", name: "TOPIX",      kind: "INDEX_JP", symbol: "1306.T"  # ETF代理
      - code: "SPX",   name: "S&P500",     kind: "INDEX_US", symbol: "^GSPC"
      - code: "NDX",   name: "NASDAQ100",  kind: "INDEX_US", symbol: "^NDX"
      - code: "USDJPY",name: "ドル円",     kind: "FX",       symbol: "JPY=X"
      - code: "VIX",   name: "VIX指数",    kind: "VOL",      symbol: "^VIX"
    """

    KIND_CHOICES = [
        ("INDEX_JP", "日本株指数"),
        ("INDEX_US", "米国株指数"),
        ("INDEX_EU", "欧州株指数"),
        ("INDEX_ASIA", "アジア株指数"),
        ("FUTURE", "先物"),
        ("FX", "為替"),
        ("VOL", "ボラティリティ指数"),
        ("OTHER", "その他"),
    ]

    code = models.CharField(
        max_length=32,
        unique=True,
        help_text="内部用コード（例: NK225, TOPIX, SPX, USDJPY など）",
    )
    name = models.CharField(
        max_length=64,
        help_text="表示名（例: 日経平均, TOPIX, S&P500, ドル円など）",
    )
    kind = models.CharField(
        max_length=16,
        choices=KIND_CHOICES,
        help_text="指数の種別（日本株指数 / 米国株指数 / 為替など）",
    )
    symbol = models.CharField(
        max_length=64,
        help_text="価格取得用ティッカー（yfinance 等で使うシンボル）",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="取得対象とするかどうかのフラグ",
    )
    sort_order = models.IntegerField(
        default=0,
        help_text="一覧表示順などに使う任意の並び順",
    )

    class Meta:
        verbose_name = "ベンチマークマスタ"
        verbose_name_plural = "ベンチマークマスタ"
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return f"{self.code} ({self.name})"


class BenchmarkPrice(models.Model):
    """
    ベンチマークの日足OHLCV
    index=日付、columns=["Open","High","Low","Close","Volume"]
    """

    benchmark = models.ForeignKey(
        BenchmarkMaster,
        on_delete=models.CASCADE,
        related_name="prices",
    )
    date = models.DateField(db_index=True)

    open = models.FloatField(null=True, blank=True)
    high = models.FloatField(null=True, blank=True)
    low = models.FloatField(null=True, blank=True)
    close = models.FloatField(null=True, blank=True)
    volume = models.FloatField(
        null=True,
        blank=True,
        help_text="出来高（指数の場合は 0 や NaN を許容）",
    )

    class Meta:
        verbose_name = "ベンチマーク価格"
        verbose_name_plural = "ベンチマーク価格"
        unique_together = ("benchmark", "date")
        indexes = [
            models.Index(fields=["benchmark", "date"]),
        ]
        ordering = ["benchmark", "date"]

    def __str__(self):
        return f"{self.benchmark.code} {self.date} close={self.close}"


class MacroRegimeSnapshot(models.Model):
    """
    日付ごとの「相場レジーム」まとめ。
    """

    date = models.DateField(
        db_index=True,
        unique=True,
        help_text="このレジームが表す営業日（日足ベース）",
    )

    # --- 日本株 ---
    jp_trend_score = models.FloatField(null=True, blank=True)
    jp_trend_label = models.CharField(max_length=16, blank=True)

    # --- 米国株 ---
    us_trend_score = models.FloatField(null=True, blank=True)
    us_trend_label = models.CharField(max_length=16, blank=True)

    # --- 為替 ---
    fx_trend_score = models.FloatField(null=True, blank=True)
    fx_trend_label = models.CharField(max_length=16, blank=True)

    # --- ボラティリティ ---
    vol_level = models.FloatField(null=True, blank=True)
    vol_label = models.CharField(max_length=16, blank=True)

    # --- 総合 ---
    regime_score = models.FloatField(null=True, blank=True)
    regime_label = models.CharField(max_length=16, blank=True)

    # --- テロップ用（DBに保持する版）---
    summary = models.CharField(
        max_length=255,
        blank=True,
        help_text="画面テロップ用の一行サマリ（services 側で生成して保存）",
    )

    # --- 詳細 ---
    detail_json = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "マクロレジームスナップショット"
        verbose_name_plural = "マクロレジームスナップショット"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.date} {self.regime_label or ''}"

    # =========================================================
    # 自動生成（Pythonロジック用） summary_text
    # =========================================================
    @property
    def summary_text(self) -> str:
        """
        DB保存版とは別に「毎回動的に生成する summary」。
        services 側で summary フィールドへ保存するときの元になる。
        """
        jp = self.jp_trend_label or "?"
        us = self.us_trend_label or "?"
        fx = self.fx_trend_label or "?"
        vol = self.vol_label or "?"
        reg = self.regime_label or "?"
        return f"日本株: {jp} / 米国株: {us} / 為替: {fx} / ボラ: {vol} / 総合: {reg}"

    # =========================================================
    # 総合レーティング（A〜E）
    # =========================================================
    @property
    def regime_grade(self) -> str:
        s = self.regime_score
        if s is None:
            return "-"
        if s >= 0.6:
            return "A"
        if s >= 0.2:
            return "B"
        if s > -0.2:
            return "C"
        if s > -0.6:
            return "D"
        return "E"