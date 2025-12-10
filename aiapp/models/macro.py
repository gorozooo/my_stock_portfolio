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
      - code: "TOPIX", name: "TOPIX",      kind: "INDEX_JP", symbol: "1306.T"  # ETF で代理
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

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code} ({self.name})"


class BenchmarkPrice(models.Model):
    """
    ベンチマークの日足OHLCV
    index=日付、columns=["Open","High","Low","Close","Volume"] 相当。
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

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.benchmark.code} {self.date} close={self.close}"


class MacroRegimeSnapshot(models.Model):
    """
    日付ごとの「相場レジーム」まとめ。

    例:
      - jp_trend_label:  "UP" / "DOWN" / "FLAT"
      - us_trend_label:  "UP" / "DOWN" / "FLAT"
      - fx_trend_label:  "YEN_WEAK" / "YEN_STRONG" / "NEUTRAL"
      - vol_label:       "CALM" / "ELEVATED" / "HIGH"
      - regime_label:    "RISK_ON" / "RISK_OFF" / "NEUTRAL"

    数値スコア（*_score）は -1.0〜+1.0 程度を想定。
    ロジックは services.macro_regime 側で実装し、
    ここでは結果だけを保存する。
    """

    date = models.DateField(
        db_index=True,
        unique=True,
        help_text="このレジームが表す営業日（日足ベース）",
    )

    # --- 日本株ゾーン（日経 / TOPIX 等から集約） ---
    jp_trend_score = models.FloatField(
        null=True,
        blank=True,
        help_text="日本株トレンド強度スコア（-1〜+1 目安）",
    )
    jp_trend_label = models.CharField(
        max_length=16,
        blank=True,
        help_text="日本株トレンドのラベル（UP / DOWN / FLAT など）",
    )

    # --- 米国株ゾーン（S&P500 / NASDAQ 等） ---
    us_trend_score = models.FloatField(
        null=True,
        blank=True,
        help_text="米国株トレンド強度スコア（-1〜+1 目安）",
    )
    us_trend_label = models.CharField(
        max_length=16,
        blank=True,
        help_text="米国株トレンドのラベル（UP / DOWN / FLAT など）",
    )

    # --- 為替ゾーン（ドル円など） ---
    fx_trend_score = models.FloatField(
        null=True,
        blank=True,
        help_text="為替トレンド強度スコア（-1〜+1 目安、円安/円高）",
    )
    fx_trend_label = models.CharField(
        max_length=16,
        blank=True,
        help_text="為替トレンドのラベル（YEN_WEAK / YEN_STRONG / NEUTRAL 等）",
    )

    # --- ボラティリティ（VIX 等） ---
    vol_level = models.FloatField(
        null=True,
        blank=True,
        help_text="ボラティリティ水準（例: 正規化したVIXスコア）",
    )
    vol_label = models.CharField(
        max_length=16,
        blank=True,
        help_text="ボラティリティのラベル（CALM / ELEVATED / HIGH など）",
    )

    # --- 総合レジーム ---
    regime_score = models.FloatField(
        null=True,
        blank=True,
        help_text="総合レジームスコア（-1.0: 強リスクオフ, +1.0: 強リスクオン）",
    )
    regime_label = models.CharField(
        max_length=16,
        blank=True,
        help_text="総合レジームのラベル（RISK_ON / RISK_OFF / NEUTRAL 等）",
    )

    # --- 詳細情報（デバッグ / 可視化用） ---
    detail_json = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "各ベンチマークの MA / RSI / RET / SLOPE 等の生データを "
            "まとめて入れておくためのフィールド。"
        ),
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        help_text="このスナップショットを生成した日時",
    )

    class Meta:
        verbose_name = "マクロレジームスナップショット"
        verbose_name_plural = "マクロレジームスナップショット"
        ordering = ["-date"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.date} {self.regime_label or ''}"

    # =========================================================
    # 追加: サマリ文字列 & グレード（A〜E）
    # =========================================================
    @property
    def summary(self) -> str:
        """
        画面テロップやログ用の要約文字列。
        例: "日本株: UP / 米国株: FLAT / 為替: YEN_WEAK / ボラ: CALM / 総合: RISK_ON"
        """
        jp = self.jp_trend_label or "?"
        us = self.us_trend_label or "?"
        fx = self.fx_trend_label or "?"
        vol = self.vol_label or "?"
        reg = self.regime_label or "?"
        return f"日本株: {jp} / 米国株: {us} / 為替: {fx} / ボラ: {vol} / 総合: {reg}"

    @property
    def regime_grade(self) -> str:
        """
        regime_score からざっくり A〜E のレーティングを出す。
        （フロントでバッジ表示などに使う想定）
        """
        if self.regime_score is None:
            return "-"
        s = self.regime_score
        if s >= 0.6:
            return "A"
        if s >= 0.2:
            return "B"
        if s > -0.2:
            return "C"
        if s > -0.6:
            return "D"
        return "E"