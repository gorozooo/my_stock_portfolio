# advisor/services/notify.py
from __future__ import annotations
import os, json, requests
from django.utils.timezone import localtime

def push_line_message(ticker: str, name: str, policy: str, win_prob: float, tp_price: float, sl_price: float,
                      reasons: list[str], confidence: float, theme_score: float):
    """
    LINE通知を送信する（トレードカード風テンプレ）
    """
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    uids = [u.strip() for u in os.getenv("LINE_TO_USER_IDS", "").split(",") if u.strip()]
    if not token or not uids:
        print("⚠️ LINEトークンまたはユーザーID未設定 (.env を確認)")
        return

    # ===== メッセージ整形 =====
    text = (
        f"📈 {policy} 候補: {ticker} {name}\n"
        f"信頼度: {int(confidence*100)}%（テーマ強度: {theme_score:.2f}）\n"
        f"想定: TP {tp_price:.0f} / SL {sl_price:.0f}\n"
        f"理由: {', '.join(reasons[:3])}\n\n"
        f"🔸 次のアクション\n"
        f"[発注メモに保存] [2h後リマインド] [却下]"
    )

    LINE_API = "https://api.line.me/v2/bot/message/push"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    for uid in uids:
        r = requests.post(LINE_API, headers=headers,
            data=json.dumps({
                "to": uid,
                "messages": [{"type": "text", "text": text}]
            })
        )
        print("📩", uid, r.status_code, r.text)