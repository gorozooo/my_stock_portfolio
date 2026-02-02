from django.conf import settings
from django.db import models
from django.utils import timezone


# =========================================================
# 1) iPhone 1画面ダッシュボード用（日次状態）
# =========================================================
class AutoTradeDailyState(models.Model):
    """
    iPhone 1画面に表示する「今日の状態」を1レコードに集約
    """
    date = models.DateField(unique=True)

    # 🟢 FULL / 🟡 LIGHT / 🔴 STOP
    gate_level = models.CharField(max_length=10, default="STOP")
    gate_reason = models.TextField(blank=True, default="")

    # 資産（将来：証券会社API等から更新）
    equity_yen = models.BigIntegerField(
        default=getattr(settings, "AUTOTRADE_BASE_EQUITY_YEN", 1_000_000)
    )
    pnl_day_yen = models.IntegerField(default=0)
    pnl_total_yen = models.IntegerField(default=0)

    # 今日の戦略：BREAKOUT / VWAP
    strategy = models.CharField(max_length=20, blank=True, default="")
    strategy_decided_at = models.DateTimeField(null=True, blank=True)

    # ★ 追加：朝30分の統計（9:30時点で固定保存して再現性を担保）
    # 例:
    # {
    #   "range_pct": 0.0195,
    #   "trend_pct": 0.0080,
    #   "chop_ratio": 0.55,
    #   "tickers_used": ["7011.T", ...],
    #   "n_used": 10,
    #   "note": "ok"
    # }
    morning_stats = models.JSONField(default=dict, blank=True)

    # ★ 追加：戦略決定のログ（理由・confidence・debug まで保存）
    # 例:
    # {
    #   "strategy":"BREAKOUT",
    #   "confidence":0.78,
    #   "reason":"朝の値動きは大きく...",
    #   "debug":{...},
    #   "created_at":"2026-02-02T09:30:00+09:00"
    # }
    strategy_decision = models.JSONField(default=dict, blank=True)

    # 今日の銘柄（5〜10）や理由（表示用）
    # 例: {"picks":[{"ticker":"7203.T","reason":"出来高が多い"}]}
    universe = models.JSONField(default=dict, blank=True)

    # バックテスト結果（strategy別、window別）
    # 例: {"BREAKOUT":{"20":{...},"60":{...},"120":{...}}}
    backtest = models.JSONField(default=dict, blank=True)

    # 今日のルール要約（リスク、回数、時間など）
    rules = models.JSONField(default=dict, blank=True)

    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"[{self.date}] {self.gate_level} {self.strategy}".strip()


# =========================================================
# 2) 調整用プロファイル（作業台）
# =========================================================
class AutoTradeTuningProfile(models.Model):
    """
    画面で数値を調整するための「作業中の設定」

    初心者ポイント：
    - ここはいくらでも変更してOK
    - 過去のバックテスト結果は壊れません
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="autotrade_tuning_profiles",
    )

    name = models.CharField(max_length=100)

    # 数値パラメータ一式（固定スキーマ）
    params = models.JSONField()

    is_archived = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"[TUNING] {self.name}"


# =========================================================
# 3) 設定スナップショット（固定版）
# =========================================================
class AutoTradeSettingSnapshot(models.Model):
    """
    ある時点の設定を「丸ごと固定」したスナップショット

    初心者ポイント：
    - バックテストや本番は必ずこれを使う
    - 後から数値が変わらない＝再現性100%
    """

    STATUS_CHOICES = (
        ("DRAFT", "下書き"),
        ("CANDIDATE", "本番候補"),
        ("ACTIVE", "本番採用中"),
        ("RETIRED", "引退"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="autotrade_snapshots",
    )

    source_profile = models.ForeignKey(
        AutoTradeTuningProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="snapshots",
    )

    label = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")

    # 固定された数値パラメータ
    snapshot = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[SNAPSHOT] {self.label} ({self.status})"


# =========================================================
# 4) バックテスト実行ログ
# =========================================================
class AutoTradeBacktestRun(models.Model):
    """
    バックテストを1回実行した結果を保存

    初心者ポイント：
    - 「どの設定で、どんな結果だったか」が後から全部追える
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="autotrade_backtests",
    )

    snapshot = models.ForeignKey(
        AutoTradeSettingSnapshot,
        on_delete=models.CASCADE,
        related_name="backtest_runs",
    )

    # 例: "JPX_TOP200_20260201"
    universe_version = models.CharField(max_length=100)

    # 例: "20/60/120"
    period_set = models.CharField(max_length=20)

    # 結果サマリー
    # 必須キー：
    # - total_pnl_yen
    # - win_rate_pct
    # - ev_per_trade_yen
    # - pf
    # - max_dd_pct
    # - trades
    result_summary = models.JSONField()

    # 🟢 / 🟡 / 🔴
    gate_result = models.CharField(max_length=10)

    # 日本語での判定理由
    reason_text = models.TextField(blank=True, default="")

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"[BACKTEST] {self.snapshot.label} {self.gate_result}"