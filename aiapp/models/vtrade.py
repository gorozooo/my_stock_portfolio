# aiapp/models/vtrade.py
from __future__ import annotations

from django.conf import settings
from django.db import models


class VirtualTrade(models.Model):
    """
    Paper trade (virtual) stored in DB.

    JSONL(sim_orders_YYYY-MM-DD.jsonl) remains the "pipeline truth" for
    behavior dataset / model training.
    This model is the "state truth" for UI and for aggregations (⭐️/confidence).

    ★ C（1口座・PRO統一）対応：
    - broker別（楽天/SBI/松井）は “残す”（互換・デバッグ用）
    - 代わりに PRO（統一口座）列を追加し、ランキング/EV_true/学習は基本こちらを見る
    """

    # ---- identity / linkage ----
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="aiapp_vtrades",
    )
    run_id = models.CharField(max_length=64, db_index=True)   # e.g. 20251125_070001_auto_demo
    run_date = models.DateField(db_index=True)                # YYYY-MM-DD (batch date)
    trade_date = models.DateField(db_index=True)              # evaluation base date (trade start date)
    source = models.CharField(max_length=64, default="ai_simulate_auto")  # who created this trade
    mode = models.CharField(max_length=16, default="demo")    # live/demo/all etc

    # ---- stock info ----
    code = models.CharField(max_length=8, db_index=True)
    name = models.CharField(max_length=64, blank=True, default="")
    sector = models.CharField(max_length=64, blank=True, default="")
    side = models.CharField(max_length=8, default="BUY")      # BUY/SELL

    # ---- pick meta ----
    universe = models.CharField(max_length=32, blank=True, default="")
    style = models.CharField(max_length=16, blank=True, default="aggressive")
    horizon = models.CharField(max_length=16, blank=True, default="short")
    topk = models.IntegerField(null=True, blank=True)

    score = models.FloatField(null=True, blank=True)
    score_100 = models.IntegerField(null=True, blank=True)
    stars = models.IntegerField(null=True, blank=True)

    # ---- mode (period/aggr) for future expansion ----
    mode_period = models.CharField(max_length=8, blank=True, default="short")  # short/mid/long
    mode_aggr = models.CharField(max_length=8, blank=True, default="aggr")     # aggr/norm/def

    # ---- AI snapshot (do not change) ----
    entry_px = models.FloatField(null=True, blank=True)  # AI entry
    tp_px = models.FloatField(null=True, blank=True)
    sl_px = models.FloatField(null=True, blank=True)
    last_close = models.FloatField(null=True, blank=True)

    # =========================================================
    # C（1口座・PRO統一）: sizing / expected (統一口座)
    # =========================================================
    qty_pro = models.IntegerField(null=True, blank=True)                 # 統一口座の数量
    required_cash_pro = models.FloatField(null=True, blank=True)         # 統一口座の必要資金（概算）
    est_pl_pro = models.FloatField(null=True, blank=True)                # 統一口座の想定PL（円）
    est_loss_pro = models.FloatField(null=True, blank=True)              # 統一口座の想定損失（円、あなたの慣習では負値が多い）

    # =========================================================
    # 既存: sizing per broker（互換/デバッグ用に残す）
    # =========================================================
    qty_rakuten = models.IntegerField(null=True, blank=True)
    qty_sbi = models.IntegerField(null=True, blank=True)
    qty_matsui = models.IntegerField(null=True, blank=True)

    required_cash_rakuten = models.FloatField(null=True, blank=True)
    required_cash_sbi = models.FloatField(null=True, blank=True)
    required_cash_matsui = models.FloatField(null=True, blank=True)

    est_pl_rakuten = models.FloatField(null=True, blank=True)
    est_pl_sbi = models.FloatField(null=True, blank=True)
    est_pl_matsui = models.FloatField(null=True, blank=True)

    est_loss_rakuten = models.FloatField(null=True, blank=True)
    est_loss_sbi = models.FloatField(null=True, blank=True)
    est_loss_matsui = models.FloatField(null=True, blank=True)

    # ---- lifecycle ----
    opened_at = models.DateTimeField(db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # =========================================================
    # C（1口座・PRO統一）: evaluation / rank / EV_true
    # =========================================================
    status = models.CharField(
        max_length=16,
        blank=True,
        default="OPEN",
        db_index=True,
        help_text="OPEN/TP/SL/EXPIRE/NO_POSITION/ERROR など",
    )
    evaluated_at = models.DateTimeField(null=True, blank=True, db_index=True)

    eval_label = models.CharField(max_length=16, blank=True, default="")  # win/lose/flat/no_position
    eval_pl = models.FloatField(null=True, blank=True)                    # 統一口座の確定PL（円）
    result_r = models.FloatField(null=True, blank=True)                   # 統一口座のR（PL/|想定損失|）
    ev_true = models.FloatField(null=True, blank=True)                    # -1〜+1 想定（真の期待値）
    rank = models.IntegerField(null=True, blank=True, db_index=True)      # UI用ランキング

    fees_total = models.FloatField(
        null=True,
        blank=True,
        help_text="手数料+スリッページ等の合計（円）。eval_pl を作る時に控除したい場合に使う",
    )

    # =========================================================
    # 既存: evaluation (filled / exit) （互換のため残す）
    # =========================================================
    eval_entry_px = models.FloatField(null=True, blank=True)
    eval_entry_ts = models.DateTimeField(null=True, blank=True)
    eval_exit_px = models.FloatField(null=True, blank=True)
    eval_exit_ts = models.DateTimeField(null=True, blank=True)
    eval_exit_reason = models.CharField(max_length=32, blank=True, default="")
    eval_horizon_days = models.IntegerField(null=True, blank=True)

    eval_label_rakuten = models.CharField(max_length=16, blank=True, default="")
    eval_label_sbi = models.CharField(max_length=16, blank=True, default="")
    eval_label_matsui = models.CharField(max_length=16, blank=True, default="")

    eval_pl_rakuten = models.FloatField(null=True, blank=True)
    eval_pl_sbi = models.FloatField(null=True, blank=True)
    eval_pl_matsui = models.FloatField(null=True, blank=True)

    # ---- R評価（まずは “想定損失” を分母にする簡易R） ----
    result_r_rakuten = models.FloatField(null=True, blank=True)
    result_r_sbi = models.FloatField(null=True, blank=True)
    result_r_matsui = models.FloatField(null=True, blank=True)

    # ---- replay / raw payload ----
    replay = models.JSONField(default=dict)  # keep raw snapshot / debug etc
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "aiapp_virtual_trade"
        indexes = [
            models.Index(fields=["code", "opened_at"]),
            models.Index(fields=["user", "trade_date"]),
            models.Index(fields=["user", "run_date"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "trade_date", "status"]),
            models.Index(fields=["user", "trade_date", "rank"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["user", "run_id", "code"], name="uq_aiapp_vtrade_user_runid_code"),
        ]

    def __str__(self) -> str:
        return f"{self.name}({self.code}) {self.opened_at:%Y-%m-%d}"

    @staticmethod
    def _safe_r(pl: float | None, est_loss: float | None) -> float | None:
        """
        R = PL / |想定損失|
        - est_loss が負値でも abs() で扱う
        """
        try:
            if pl is None or est_loss is None:
                return None
            denom = abs(float(est_loss))
            if denom <= 0:
                return None
            return float(pl) / denom
        except Exception:
            return None

    def recompute_r(self) -> None:
        """
        既存（楽天/SBI/松井） + C（PRO統一）をまとめて更新する。
        ai_sim_eval 側で eval_pl / est_loss_* を埋めたあとに呼ぶ想定。
        """
        # --- C（PRO統一） ---
        self.result_r = self._safe_r(self.eval_pl, self.est_loss_pro)

        # --- 既存（互換/デバッグ） ---
        self.result_r_rakuten = self._safe_r(self.eval_pl_rakuten, self.est_loss_rakuten)
        self.result_r_sbi = self._safe_r(self.eval_pl_sbi, self.est_loss_sbi)
        self.result_r_matsui = self._safe_r(self.eval_pl_matsui, self.est_loss_matsui)