from __future__ import annotations
import os, json, requests
from typing import Any, Dict, List, Optional

LINE_PUSH_API = "https://api.line.me/v2/bot/message/push"

def _env_token() -> Optional[str]:
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

def _env_user_ids() -> List[str]:
    raw = os.getenv("LINE_TO_USER_IDS", "") or os.getenv("LINE_USER_ID", "")
    return [u.strip() for u in raw.split(",") if u.strip()]

def push_line_message(alt_text: str, *, flex: Optional[Dict[str, Any]] = None, text: Optional[str] = None) -> None:
    """
    alt_text …… 通知バナー等に出る短文（必須）
    flex     …… Flex Messageのbubbleまたはcarousel（推薦）
    text     …… プレーンテキスト（フォールバック用・任意）
    """
    token = _env_token()
    uids  = _env_user_ids()
    if not token or not uids:
        # 診断ログ（管理コマンド側の --why でも出す）
        print(f"[LINE diag] TOKEN={'set' if token else 'missing'} UIDS={len(uids)}")
        return

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 送信するmessage配列を作る
    messages: List[Dict[str, Any]] = []
    if flex:
        messages.append({
            "type": "flex",
            "altText": alt_text[:240],
            "contents": flex,  # bubble or carousel
        })
    if text:
        messages.append({"type": "text", "text": text})

    payloads = [{"to": uid, "messages": messages} for uid in uids]

    for p in payloads:
        r = requests.post(LINE_PUSH_API, headers=headers, data=json.dumps(p))
        if r.status_code >= 300:
            print("[LINE error]", r.status_code, r.text)

# ===== Flex ビルダー =====

def make_flex_from_tr(tr, policies: List[str], *, window: str = "preopen") -> Dict[str, Any]:
    """
    TrendResult 相当の tr（ticker, name, overall_score, weekly_trend, slope_annual, theme_score, entry_price_hint 等）
    と、ヒットしたポリシー名から “1枚カード” を作る。
    """
    ticker = (tr.ticker or "").upper()
    name   = (tr.name or ticker)
    score  = int(tr.overall_score or 0)
    trend  = (tr.weekly_trend or "-")
    slope  = round(float(tr.slope_annual or 0.0) * 100, 1)  # %/yr
    theme  = round(float(tr.theme_score or 0.0) * 100)

    # ざっくり目標/損切（あれば notes の計算済を使ってもOK）
    entry = int(tr.entry_price_hint or tr.close_price or 0) or None

    def stat_box(label: str, value: str) -> Dict[str, Any]:
        return {
            "type": "box", "layout": "vertical", "flex": 1, "contents": [
                {"type": "text", "text": label, "size": "xs", "color": "#99A3B3"},
                {"type": "text", "text": value, "weight": "bold", "size": "md"}
            ]
        }

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box", "layout": "vertical", "paddingAll": "12px",
            "contents": [
                {"type": "text", "text": f"[{window}] {ticker}", "weight": "bold", "size": "md"},
                {"type": "text", "text": name, "wrap": True, "size": "sm", "color": "#dfe7f3"}
            ],
            "backgroundColor": "#0b1526"
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "12px", "backgroundColor": "#0f1a30",
            "contents": [
                {
                    "type": "box", "layout": "horizontal", "spacing": "12px",
                    "contents": [
                        stat_box("Score", f"{score}"),
                        stat_box("Weekly", trend),
                        stat_box("Slope", f"{slope}%/yr"),
                        stat_box("Theme", f"{theme}")
                    ]
                },
                {"type": "separator", "color": "#22304a"},
                {
                    "type": "box", "layout": "vertical", "spacing": "6px",
                    "contents": [
                        {"type": "text", "text": "Policies", "size": "xs", "color": "#99A3B3"},
                        {"type": "text", "text": " / ".join(policies)[:480], "wrap": True, "size": "sm"}
                    ]
                },
            ] + (
                [] if not entry else [
                    {"type": "separator", "color": "#22304a"},
                    {
                        "type": "box", "layout": "horizontal", "contents": [
                            stat_box("Entry", f"¥{entry:,}"),
                            stat_box("TP/SL", "ポリシー準拠")
                        ]
                    }
                ]
            )
        },
        "footer": {
            "type": "box", "layout": "vertical", "spacing": "8px",
            "contents": [
                {
                    "type": "button", "style": "primary", "height": "sm",
                    "action": {"type": "message", "label": "発注メモに保存", "text": f"/save {ticker}"}
                },
                {
                    "type": "button", "style": "secondary", "height": "sm",
                    "action": {"type": "message", "label": "2時間後に再通知", "text": f"/remind2h {ticker}"}
                },
                {
                    "type": "button", "style": "link", "height": "sm",
                    "action": {"type": "message", "label": "今回は見送り", "text": f"/reject {ticker}"}
                }
            ]
        },
        "styles": {"body": {"separator": True}}
    }
    return bubble