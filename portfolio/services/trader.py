from decimal import Decimal
from datetime import datetime
from django.utils import timezone
from ..models import Position
from .notifier import send_line_message
from .metrics import get_latest_price  # ← あなたの既存APIで終値取得する関数を利用

# ==============
# STOP / TP 判定
# ==============
def evaluate_positions():
    """全OPENポジションをスキャンしてSTOP/TPを自動判定"""
    positions = Position.objects.filter(state="OPEN")
    for pos in positions:
        try:
            price = get_latest_price(pos.ticker)
            if not price:
                continue

            # R値換算
            risk_per_share = abs(pos.entry_price - pos.stop_price)
            current_R = (price - pos.entry_price) / risk_per_share
            if pos.side == "SHORT":
                current_R *= -1

            pos.max_MFE_R = max(pos.max_MFE_R or -99, current_R)
            pos.max_MAE_R = min(pos.max_MAE_R or 99, current_R)

            # STOP判定
            stop_hit = (
                pos.side == "LONG" and price <= pos.stop_price
            ) or (
                pos.side == "SHORT" and price >= pos.stop_price
            )

            if stop_hit:
                close_position(pos, price, reason="STOP")
                continue

            # TP判定（複数）
            for i, target in enumerate(pos.targets):
                if pos.side == "LONG" and price >= target:
                    partial_take(pos, price, target, i)
                if pos.side == "SHORT" and price <= target:
                    partial_take(pos, price, target, i)

        except Exception as e:
            print("evaluate_positions error:", e)


# ============
# 部分利確
# ============
def partial_take(pos, price, target, idx):
    send_line_message(
        f"💰 TP{idx+1}: {pos.ticker} 到達 {target:.0f}円 (+{round((target-pos.entry_price)/abs(pos.entry_price-pos.stop_price),2)}R)"
    )
    # 最終TPならクローズ
    if idx == len(pos.targets) - 1:
        close_position(pos, price, reason="TP")


# ============
# クローズ処理
# ============
def close_position(pos, price, reason="TP"):
    risk_per_share = abs(pos.entry_price - pos.stop_price)
    pnl_R = (price - pos.entry_price) / risk_per_share
    if pos.side == "SHORT":
        pnl_R *= -1
    pos.pnl_R = float(pnl_R)
    pos.pnl_yen = float(pnl_R * risk_per_share * pos.qty)
    pos.state = "CLOSED"
    pos.closed_at = timezone.now()
    pos.save()
    send_line_message(
        f"📊 CLOSED: {pos.ticker} {pos.side} {reason} {pnl_R:+.2f}R ({pos.pnl_yen:,.0f}円)"
    )