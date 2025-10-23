from datetime import datetime
from django.utils import timezone
from django.conf import settings
from ..models import Position
from .line_notify import send_line_message
from .metrics import get_latest_price  # ← 終値取得関数を既存のものに合わせて利用

# ===========================
# STOP / TP 自動判定メイン処理
# ===========================
def evaluate_positions():
    """保有中ポジションをスキャンして STOP / TP / トレールを自動判定"""
    positions = Position.objects.filter(state="OPEN")
    for pos in positions:
        try:
            price = get_latest_price(pos.ticker)
            if not price:
                continue

            # --- R値算出 ---
            risk_per_share = abs(pos.entry_price - pos.stop_price)
            current_R = (price - pos.entry_price) / risk_per_share
            if pos.side == "SHORT":
                current_R *= -1

            # --- 最大変動更新 ---
            pos.max_MFE_R = max(pos.max_MFE_R or -99, current_R)
            pos.max_MAE_R = min(pos.max_MAE_R or 99, current_R)

            # --- STOP 判定 ---
            stop_hit = (
                (pos.side == "LONG" and price <= pos.stop_price)
                or (pos.side == "SHORT" and price >= pos.stop_price)
            )

            if stop_hit:
                close_position(pos, price, reason="STOP")
                continue

            # --- TP 判定（複数） ---
            for i, target in enumerate(pos.targets):
                if pos.side == "LONG" and price >= target:
                    partial_take(pos, price, target, i)
                if pos.side == "SHORT" and price <= target:
                    partial_take(pos, price, target, i)

            pos.save()

        except Exception as e:
            print(f"[ERROR] evaluate_positions: {e}")


# ===========================
# 部分利確処理
# ===========================
def partial_take(pos, price, target, idx):
    """部分利確通知（数量は今後のフェーズで実装）"""
    r_gain = (target - pos.entry_price) / abs(pos.entry_price - pos.stop_price)
    if pos.side == "SHORT":
        r_gain *= -1

    msg = (
        f"💰 TP{idx+1}: {pos.ticker} 到達 {target:,.0f}円 "
        f"({r_gain:+.2f}R) / {pos.side}"
    )
    push_line_to_user(pos, msg)

    # 最終ターゲットならクローズ
    if idx == len(pos.targets) - 1:
        close_position(pos, price, reason=f"TP{idx+1}")


# ===========================
# クローズ処理（STOP or 最終TP）
# ===========================
def close_position(pos, price, reason="TP"):
    """ポジションをクローズし損益を記録"""
    risk_per_share = abs(pos.entry_price - pos.stop_price)
    pnl_R = (price - pos.entry_price) / risk_per_share
    if pos.side == "SHORT":
        pnl_R *= -1

    pos.pnl_R = float(pnl_R)
    pos.pnl_yen = float(pnl_R * risk_per_share * pos.qty)
    pos.state = "CLOSED"
    pos.closed_at = timezone.now()
    pos.save()

    msg = (
        f"📊 CLOSED: {pos.ticker} {pos.side} {reason}\n"
        f"{pnl_R:+.2f}R ({pos.pnl_yen:,.0f}円)\n"
        f"建玉: {pos.entry_price:,.0f} / 終値: {price:,.0f}"
    )
    push_line_to_user(pos, msg)


# ===========================
# LINE通知（ユーザー単位送信）
# ===========================
def push_line_to_user(pos, message: str):
    """
    Position の user に紐づく line_user_id へ送信。
    user.profile.line_user_id が存在する前提。
    """
    try:
        user = pos.user
        if hasattr(user, "profile") and getattr(user.profile, "line_user_id", None):
            user_id = user.profile.line_user_id
            send_line_message(user_id, message)
        else:
            print(f"[WARN] {user.username} に line_user_id 未設定 → 通知スキップ")
    except Exception as e:
        print(f"[LINE SEND ERROR] {e}")