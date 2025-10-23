# portfolio/services/trader.py
from django.utils import timezone
from ..models import Position
from .line_notify import send_line_message        # ← あなたの既存Bot送信
from .metrics import get_latest_price             # ← 下記(§6)で追記する小関数を利用

def evaluate_positions():
    """
    保有中のポジションを自動評価（STOP/TP/クローズ）してLINE通知
    """
    positions = Position.objects.filter(state="OPEN")
    for pos in positions:
        try:
            price = get_latest_price(pos.ticker)
            if price is None:
                continue

            risk_per_share = abs(pos.entry_price - pos.stop_price)
            if risk_per_share <= 0:
                continue

            # R換算の現在値
            current_R = (price - pos.entry_price) / risk_per_share
            if pos.side == "SHORT":
                current_R *= -1

            # MFE/MAE更新
            pos.max_MFE_R = max(pos.max_MFE_R or -99, current_R)
            pos.max_MAE_R = min(pos.max_MAE_R or  99, current_R)

            # STOP判定
            hit_stop = (pos.side == "LONG"  and price <= pos.stop_price) or \
                       (pos.side == "SHORT" and price >= pos.stop_price)
            if hit_stop:
                _close_position(pos, price, reason="STOP")
                continue

            # TP判定（複数）
            for i, target in enumerate(pos.targets or []):
                if (pos.side == "LONG"  and price >= target) or \
                   (pos.side == "SHORT" and price <= target):
                    _partial_take(pos, price, target, i)

            pos.save()

        except Exception as e:
            print(f"[evaluate_positions] {pos.ticker}: {e}")

def _partial_take(pos, price, target, idx):
    r_gain = (target - pos.entry_price) / abs(pos.entry_price - pos.stop_price)
    if pos.side == "SHORT":
        r_gain *= -1
    _push_line(pos, f"💰 TP{idx+1}: {pos.ticker} 到達 {target:,.0f}円 ({r_gain:+.2f}R) / {pos.side}")
    # 最終ターゲットなら自動クローズ
    if idx == len(pos.targets) - 1:
        _close_position(pos, price, reason=f"TP{idx+1}")

def _close_position(pos, price, reason="TP"):
    risk_per_share = abs(pos.entry_price - pos.stop_price)
    pnl_R = (price - pos.entry_price) / risk_per_share
    if pos.side == "SHORT":
        pnl_R *= -1

    pos.pnl_R = float(pnl_R)
    pos.pnl_yen = float(pnl_R * risk_per_share * pos.qty)
    pos.state = "CLOSED"
    pos.closed_at = timezone.now()
    pos.save()

    _push_line(
        pos,
        f"📊 CLOSED: {pos.ticker} {pos.side} {reason}\n"
        f"{pnl_R:+.2f}R ({pos.pnl_yen:,.0f}円)\n"
        f"建値: {pos.entry_price:,.0f} / 終値: {price:,.0f}"
    )

def _push_line(pos, message: str):
    """
    ユーザー単位でBot通知（user.profile.line_user_id を想定）
    """
    u = pos.user
    user_id = getattr(getattr(u, "profile", None), "line_user_id", None)
    if not user_id:
        print(f"[LINE] skip (user_id missing) {u}")
        return
    try:
        send_line_message(user_id, message)
    except Exception as e:
        print(f"[LINE] send error: {e}")