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
    flex     …… Flex Messageのbubbleまたはcarousel（推奨）
    text     …… プレーンテキスト（フォールバック用・任意）
    """
    token = _env_token()
    uids  = _env_user_ids()
    if not token or not uids:
        print(f"[LINE diag] TOKEN={'set' if token else 'missing'} UIDS={len(uids)}")
        return

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    messages: List[Dict[str, Any]] = []
    if flex:
        messages.append({"type": "flex", "altText": alt_text[:240], "contents": flex})
    if text:
        messages.append({"type": "text", "text": text})

    for uid in uids:
        payload = {"to": uid, "messages": messages}
        r = requests.post(LINE_PUSH_API, headers=headers, data=json.dumps(payload))
        if r.status_code >= 300:
            print("[LINE error]", r.status_code, r.text)

# ===== Flex ビルダー（日本語ラベル／TP/SL表示／postbackボタン） =====

def make_flex_from_tr(
    tr,
    policies: List[str],
    *,
    window: str = "preopen",
    exits: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    tr: TrendResult ライク（ticker, name, overall_score, weekly_trend, slope_annual, theme_score, entry_price_hint 等）
    policies: ヒットしたポリシー名一覧
    exits: {'tp_price','sl_price','tp_pct','sl_pct','trail_atr_mult','time_exit_due'} を期待（無ければ非表示）
    """
    tkr   = (tr.ticker or "").upper()
    name  = (tr.name or tkr)
    score = int(tr.overall_score or 0)
    trend = (tr.weekly_trend or "-")
    slope = round(float(tr.slope_annual or 0.0) * 100, 1)  # %/yr
    theme = round(float(tr.theme_score or 0.0) * 100)
    entry = int(tr.entry_price_hint or tr.close_price or 0) or None

    def stat(label: str, value: str) -> Dict[str, Any]:
        return {
            "type": "box", "layout": "vertical", "flex": 1, "contents": [
                {"type": "text", "text": label, "size": "xs", "color": "#99A3B3"},
                {"type": "text", "text": value, "weight": "bold", "size": "md"}
            ]
        }

    # TP/SL 表示
    tp_txt = sl_txt = "（未設定）"
    if exits:
        if exits.get("tp_price"):
            tp_txt = f"目標 ¥{int(exits['tp_price']):,}（+{int(round(float(exits.get('tp_pct',0))*100))}%）"
        if exits.get("sl_price"):
            sl_txt = f"損切 ¥{int(exits['sl_price']):,}（-{int(round(float(exits.get('sl_pct',0))*100))}%）"
        if exits.get("time_exit_due"):
            sl_txt += "・時間切れ近い"

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box", "layout": "vertical", "paddingAll": "12px",
            "contents": [
                {"type": "text", "text": f"[{window}] {tkr}", "weight": "bold", "size": "md"},
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
                        stat("スコア", f"{score}"),
                        stat("週次トレンド", trend),
                        stat("傾き", f"{slope}%/年"),
                        stat("テーマ", f"{theme}")
                    ]
                },
                {"type": "separator", "color": "#22304a"},
                {
                    "type": "box", "layout": "vertical", "spacing": "6px",
                    "contents": [
                        {"type": "text", "text": "採用ポリシー", "size": "xs", "color": "#99A3B3"},
                        {"type": "text", "text": " / ".join(policies)[:480], "wrap": True, "size": "sm"}
                    ]
                },
            ] + (
                [] if not entry else [
                    {"type": "separator", "color": "#22304a"},
                    {"type": "box", "layout": "horizontal", "contents": [
                        stat("参考エントリー", f"¥{entry:,}"),
                        stat("TP/SL", tp_txt if exits else "ポリシー準拠")
                    ]},
                    *( # SL行を別枠に（見やすさ）
                        [{"type": "box", "layout": "horizontal", "contents": [stat("損切り", sl_txt) ]}]
                        if exits else []
                    )
                ]
            )
        },
        "footer": {
            "type": "box", "layout": "vertical", "spacing": "8px",
            "contents": [
                {
                    "type": "button", "style": "primary", "height": "sm",
                    "action": {
                        "type": "postback", "label": "発注メモに保存",
                        "data": f"action=save&ticker={tkr}"
                    }
                },
                {
                    "type": "button", "style": "secondary", "height": "sm",
                    "action": {
                        "type": "postback", "label": "2時間後に再通知",
                        "data": f"action=remind2h&ticker={tkr}"
                    }
                },
                {
                    "type": "button", "style": "link", "height": "sm",
                    "action": {
                        "type": "postback", "label": "今回は見送り",
                        "data": f"action=reject&ticker={tkr}"
                    }
                }
            ]
        },
        "styles": {"body": {"separator": True}}
    }
    return bubble