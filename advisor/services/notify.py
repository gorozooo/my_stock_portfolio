from __future__ import annotations
import os, json
from typing import Dict, Any, Optional, Tuple
import requests
from django.conf import settings

# ===== JPXマップの読込（和名・セクター） =====
def _load_tse_map() -> Dict[str, Dict[str, str]]:
    base = getattr(settings, "BASE_DIR", os.getcwd())
    path = os.path.join(base, "data", "tse_list.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
            # {"7203":{"name":"トヨタ自動車", ...}} の想定
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}

_TSE = _load_tse_map()

def _jpx_name(ticker: str, fallback: Optional[str]=None) -> str:
    t = (ticker or "").upper().strip()
    if t.endswith(".T"): t = t[:-2]
    v = _TSE.get(t) or {}
    nm = (v.get("name") or "").strip()
    return nm or (fallback or t)

def _yen(n: Optional[int]) -> str:
    if n is None: return "—"
    return f"¥{n:,}"

def _pct(x: Optional[float]) -> str:
    if x is None: return "—"
    return f"{round(x*100,1)}%"

def _display_ticker(t: str) -> str:
    t = (t or "").upper().strip()
    if t.isdigit() and 4 <= len(t) <= 5:
        return f"{t}.T"
    return t

# ===== Flexカード（日本語ラベル・wrap有効） =====
def build_trade_card(*, window: str, ticker: str, name: Optional[str],
                     score: Optional[int], weekly: str, slope_yr: Optional[float],
                     theme: Optional[float],
                     entry_price: Optional[int],
                     tp_price: Optional[int], sl_price: Optional[int],
                     policy_line: str) -> Dict[str, Any]:
    title = f"[{window}] {_display_ticker(ticker)}"
    jp_name = _jpx_name(ticker, name)

    def txt(text, size="sm", weight=None, color="#e5e7eb", align="start", wrap=True):
        node = {"type":"text","text":str(text),"size":size,"color":color,"wrap":wrap}
        if weight: node["weight"]=weight
        if align: node["align"]=align
        return node

    def kv(label, value):
        return {
            "type":"box","layout":"baseline","spacing":"sm","contents":[
                txt(label, size="sm", color="#94a3b8"),
                {"type":"filler"},
                txt(value, size="sm", color="#e5e7eb", align="end")
            ]
        }

    # 攻め：数字は全文表示させる（wrap true / maxLines 指定しない）
    header = {
        "type":"box","layout":"vertical","backgroundColor":"#0b1220",
        "paddingAll":"16px","contents":[
            txt(title, size="sm", color="#93c5fd", weight="bold"),
            txt(jp_name, size="md", weight="bold", color="#f8fafc"),
            {"type":"separator","margin":"12px","color":"#1f2937"},
            {"type":"box","layout":"horizontal","contents":[
                kv("スコア", str(score if score is not None else "—")),
                kv("週次トレンド", {"up":"up","down":"down"}.get(weekly, weekly or "—")),
                kv("傾き", f"{round((slope_yr or 0.0)*100,1)}%/yr"),
                kv("テーマ", str(int(round((theme or 0.0)*100))))
            ],"spacing":"md"}
        ]
    }

    policies = {
        "type":"box","layout":"vertical","paddingAll":"12px",
        "contents":[
            txt("採用ポリシー", size="sm", color="#94a3b8"),
            txt(policy_line, size="sm", color="#e5e7eb")
        ]
    }

    prices = {
        "type":"box","layout":"vertical","paddingAll":"12px",
        "contents":[
            {"type":"box","layout":"horizontal","contents":[
                kv("参考エントリー", _yen(entry_price)),
                kv("TP/SL", "ポリシー準拠"),
            ],"spacing":"md"},
            {"type":"box","layout":"horizontal","contents":[
                kv("目標(TP)", _yen(tp_price)),
                kv("損切り(SL)", _yen(sl_price)),
            ],"spacing":"md"}
        ]
    }

    def pb(text, data, style="primary"):
        return {
            "type":"button","style":style,"height":"sm",
            "action":{"type":"postback","label":text,"data":data}
        }

    buttons = {
        "type":"box","layout":"vertical","spacing":"sm","paddingAll":"16px","contents":[
            pb("発注メモに保存", f"save:{_display_ticker(ticker)}"),
            pb("2時間後に再通知", f"snooze:{_display_ticker(ticker)}:120", style="secondary"),
            pb("今回は見送り", f"reject:{_display_ticker(ticker)}", style="secondary"),
        ]
    }

    bubble = {
        "type":"bubble","size":"giga","styles":{"body":{"backgroundColor":"#0b0f1a"}},
        "body":{
            "type":"box","layout":"vertical","spacing":"sm","backgroundColor":"#0b0f1a",
            "contents":[header, {"type":"separator","color":"#1f2937"},
                        policies, {"type":"separator","color":"#1f2937"},
                        prices, buttons]
        }
    }
    return bubble

# ===== Push送信 =====
def push_line_message(payload: Dict[str, Any]) -> None:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    uids = [u.strip() for u in os.getenv("LINE_TO_USER_IDS","").split(",") if u.strip()]
    if not token or not uids:
        print("[LINE] TOKEN/UIDS not set")
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Authorization": f"Bearer {token}", "Content-Type":"application/json"}
    for uid in uids:
        body = {"to": uid, "messages":[payload]}
        r = requests.post(url, headers=headers, data=json.dumps(body))
        print("[LINE push]", uid, r.status_code, r.text[:200])