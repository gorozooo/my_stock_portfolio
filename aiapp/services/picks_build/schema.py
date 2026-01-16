# aiapp/services/picks_build/schema.py
# -*- coding: utf-8 -*-
"""
picks_build が生成する1銘柄分の出力スキーマ（JSONにそのまま落ちる）。

後方互換:
- 既存UIは追加キーを無視できる
- A側（テクニカルのみ）は追加キーが None のままでもOK
- B側（hybrid）は fund/policy/hybrid/confirm を埋める

今回の方針（B: 将来拡張前提）:
- 係数テーブル（dict）/ セクター別 weight / 寄与内訳 / 理由ログ を保存できるようにする
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class PickItem:
    code: str
    name: Optional[str] = None
    sector_display: Optional[str] = None

    entry_reason: Optional[str] = None

    # =========================
    # “確実性” 系（worker互換）
    # =========================
    confirm_score: Optional[float] = None
    confirm_flags: Optional[List[str]] = None

    chart_open: Optional[List[float]] = None
    chart_high: Optional[List[float]] = None
    chart_low: Optional[List[float]] = None
    chart_closes: Optional[List[float]] = None
    chart_dates: Optional[List[str]] = None

    chart_ma_short: Optional[List[Optional[float]]] = None
    chart_ma_mid: Optional[List[Optional[float]]] = None
    chart_ma_75: Optional[List[Optional[float]]] = None
    chart_ma_100: Optional[List[Optional[float]]] = None
    chart_ma_200: Optional[List[Optional[float]]] = None
    chart_vwap: Optional[List[Optional[float]]] = None
    chart_rsi: Optional[List[Optional[float]]] = None

    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    high_all: Optional[float] = None
    low_all: Optional[float] = None

    last_close: Optional[float] = None
    atr: Optional[float] = None

    entry: Optional[float] = None
    tp: Optional[float] = None
    sl: Optional[float] = None

    score: Optional[float] = None
    score_100: Optional[int] = None
    stars: Optional[int] = None

    ml_p_win: Optional[float] = None
    ml_ev: Optional[float] = None
    ml_rank: Optional[float] = None
    ml_hold_days_pred: Optional[float] = None
    ml_tp_first: Optional[str] = None
    ml_tp_first_probs: Optional[Dict[str, float]] = None

    ev_net_rakuten: Optional[float] = None
    rr_net_rakuten: Optional[float] = None
    ev_net_matsui: Optional[float] = None
    rr_net_matsui: Optional[float] = None
    ev_net_sbi: Optional[float] = None
    rr_net_sbi: Optional[float] = None

    ev_true_rakuten: Optional[float] = None
    ev_true_matsui: Optional[float] = None
    ev_true_sbi: Optional[float] = None

    qty_rakuten: Optional[int] = None
    required_cash_rakuten: Optional[float] = None
    est_pl_rakuten: Optional[float] = None
    est_loss_rakuten: Optional[float] = None

    qty_matsui: Optional[int] = None
    required_cash_matsui: Optional[float] = None
    est_pl_matsui: Optional[float] = None
    est_loss_matsui: Optional[float] = None

    qty_sbi: Optional[int] = None
    required_cash_sbi: Optional[float] = None
    est_pl_sbi: Optional[float] = None
    est_loss_sbi: Optional[float] = None

    reasons_text: Optional[List[str]] = None

    reason_lines: Optional[List[str]] = None
    reason_concern: Optional[str] = None

    reason_rakuten: Optional[str] = None
    reason_matsui: Optional[str] = None
    reason_sbi: Optional[str] = None

    # =========================
    # B側（hybrid）追加（後方互換）
    # =========================
    fund_score: Optional[float] = None
    fund_flags: Optional[List[str]] = None

    policy_score: Optional[float] = None
    policy_flags: Optional[List[str]] = None

    # 旧互換（前からあるキー）
    hybrid_bonus: Optional[float] = None
    ev_true_rakuten_hybrid: Optional[float] = None

    # 拡張前提（A/Bの検証ログを残す）
    hybrid_bonus_total: Optional[float] = None
    hybrid_bonus_fund: Optional[float] = None
    hybrid_bonus_policy: Optional[float] = None

    # セクター別weight（実際に使ったもの）
    hybrid_sector_weights: Optional[Dict[str, float]] = None

    # policyの中間スコア/寄与内訳（fx/rates/risk など + weights + mixed）
    hybrid_policy_components: Optional[Dict[str, Any]] = None

    # ログ用（短文）: UIで見せても良いし、後で集計にも使える
    hybrid_reason_lines: Optional[List[str]] = None