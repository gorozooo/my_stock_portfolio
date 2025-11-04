from django.db import models

class TrendResult(models.Model):
    """
    日本株の銘柄ごとに、最新日次の“判定結果”を保持するサマリテーブル。
    - 候補抽出はこのサマリを一次情報として参照（高速）
    - 原価計算（OHLCV → 指標）は別ジョブで更新
    """
    code = models.CharField('証券コード', max_length=10, db_index=True, unique=True)
    name = models.CharField('銘柄名', max_length=128)
    sector_jp = models.CharField('33業種', max_length=64, default='不明', db_index=True)

    # 最新の終値・出来高（終値基準で集計済）
    last_price = models.DecimalField('最新終値', max_digits=12, decimal_places=2)
    last_volume = models.BigIntegerField('最新出来高', default=0)

    # トレンド向き（数値）：+上昇 / 0横ばい / -下降（内部は実数、UIで⤴️➡️⤵️）
    daily_slope = models.FloatField('日足傾き', default=0.0)
    weekly_trend = models.FloatField('週足トレンド', default=0.0)
    monthly_trend = models.FloatField('月足トレンド', default=0.0)

    # 相対強度（TOPIX or 日経平均対比 >1で強い）
    rs_index = models.FloatField('相対強度(指数対比)', default=1.0)

    # 出来高スパイク倍率（直近対過去平均）
    vol_spike = models.FloatField('出来高スパイク倍率', default=1.0)

    # 補助：移動平均（閾値判定に使用）
    ma5 = models.FloatField('MA5', default=0.0)
    ma20 = models.FloatField('MA20', default=0.0)
    ma60 = models.FloatField('MA60', default=0.0)

    # 信頼度（バックテスト由来などの合成指標を格納）
    confidence = models.FloatField('AI信頼度(0-1)', default=0.0)

    # 更新メタ
    as_of = models.DateField('基準日', db_index=True, auto_now_add=False)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = 'トレンド結果'
        verbose_name_plural = 'トレンド結果'
        indexes = [
            models.Index(fields=['sector_jp']),
            models.Index(fields=['as_of']),
        ]

    def __str__(self):
        return f'{self.code} {self.name} ({self.as_of})'

    # UI用の向き（⤴️➡️⤵️）
    @property
    def dir_d(self) -> str:
        return 'up' if self.daily_slope > 0.0 else ('down' if self.daily_slope < 0.0 else 'flat')

    @property
    def dir_w(self) -> str:
        return 'up' if self.weekly_trend > 0.0 else ('down' if self.weekly_trend < 0.0 else 'flat')

    @property
    def dir_m(self) -> str:
        return 'up' if self.monthly_trend > 0.0 else ('down' if self.monthly_trend < 0.0 else 'flat')