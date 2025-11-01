from __future__ import annotations
import os, json, requests
from typing import Dict, Any, Optional, Tuple, List
from django.conf import settings

# ==== 外部: TP/SLを算出 ====
try:
    from advisor.services.policy_rules import compute_exit_targets
except Exception:
    compute_exit_targets = None  # type: ignore

# ==== モデル（名前/エントリー等の取得に使う） ====
try:
    from advisor.models_trend import TrendResult
except Exception:
    TrendResult = None  # type: ignore

# --------------------------------
# JPX名マップ（data/tse_list.json）
# --------------------------------
def _load_tse_map() -> Dict[str, Dict[str, str]]:
    base = getattr(settings, "BASE_DIR", os.getcwd())
    path = os.path.join(base, "data", "tse_list.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
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

def _display_ticker(t: str) -> str:
    t = (t or "").upper().strip()
    if t.isdigit() and 4 <= len(t) <= 5:
        return f"{t}.T"
    return t

def _yen(n: Optional[int]) -> str:
    if n is None: return "—"
    return f"¥{n:,}"

# ----------------------------------------------------
# Flexカード（ラベル日本語化・すべて wrap=True で省略なし）
# ----------------------------------------------------
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

    header = {
        "type":"box","layout":"vertical","backgroundColor":"#0b1220",
        "paddingAll":"16px","contents":[
            txt(title, size="sm", color="#93c5fd", weight="bold"),
            txt(jp_name, size="md", weight="bold", color="#f8fafc"),
            {"type":"separator","margin":"12px","color":"#1f2937"},
            {"type":"box","layout":"horizontal","spacing":"md","contents":[
                kv("スコア", str(score if score is not None else "—")),
                kv("週次トレンド", {"up":"上昇","down":"下落"}.get(weekly, weekly or "—")),
                kv("傾き", f"{round((slope_yr or 0.0)*100,1)}%/yr"),
                kv("テーマ", str(int(round((theme or 0.0)*100))))
            ]}
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
            {"type":"box","layout":"horizontal","spacing":"md","contents":[
                kv("参考エントリー", _yen(entry_price)),
                kv("TP/SL", "ポリシー準拠"),
            ]},
            {"type":"box","layout":"horizontal","spacing":"md","contents":[
                kv("目標(TP)", _yen(tp_price)),
                kv("損切り(SL)", _yen(sl_price)),
            ]},
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

    return {
        "type":"bubble","size":"giga","styles":{"body":{"backgroundColor":"#0b0f1a"}},
        "body":{
            "type":"box","layout":"vertical","spacing":"sm","backgroundColor":"#0b0f1a",
            "contents":[header, {"type":"separator","color":"#1f2937"},
                        policies, {"type":"separator","color":"#1f2937"},
                        prices, buttons]
        }
    }

# ----------------------------------------------------
# TrendResult から直接 Flex を作る（evaluate_triggers が呼ぶ）
# ----------------------------------------------------
def make_flex_from_tr(*, window: str, tr_obj: Any, policies: List[str]) -> Dict[str, Any]:
    """
    tr_obj: TrendResult（又は互換オブジェクト）
    policies: 採用されたポリシー名リスト（表示用）
    """
    # 参考エントリー
    entry = None
    try:
        entry = int(tr_obj.entry_price_hint or tr_obj.close_price or 0) or None
    except Exception:
        entry = None

    # TP/SLの具体価格（policy_rulesが使えない環境でも必ず数値を出す）
    tp_price = sl_price = None
    if compute_exit_targets is not None:
        try:
            xt = compute_exit_targets(
                policy={"targets":{}, "exits":{}},
                ticker=str(tr_obj.ticker).upper(),
                entry_price=entry,
                days_held=None,
                atr14_hint=(tr_obj.notes or {}).get("atr14") if getattr(tr_obj, "notes", None) else None,
            )
            tp_price = xt.tp_price or None
            sl_price = xt.sl_price or None
        except Exception:
            tp_price = sl_price = None

    # 最低限のフォールバック（policy_rulesが未設定でも穴を空けない）
    if entry and tp_price is None:
        tp_price = int(round(entry * 1.06))  # 目安
    if entry and sl_price is None:
        sl_price = int(round(entry * 0.98))  # 目安

    policy_line = " / ".join(policies) if policies else "—"

    return build_trade_card(
        window=window,
        ticker=str(tr_obj.ticker),
        name=(getattr(tr_obj, "name", None) or None),
        score=int(tr_obj.overall_score or 0) if tr_obj.overall_score is not None else None,
        weekly=str(tr_obj.weekly_trend or "—"),
        slope_yr=float(tr_obj.slope_annual or 0.0) if tr_obj.slope_annual is not None else None,
        theme=float(tr_obj.theme_score or 0.0) if tr_obj.theme_score is not None else None,
        entry_price=entry,
        tp_price=tp_price,
        sl_price=sl_price,
        policy_line=policy_line,
    )

# ----------------------------------------------------
# Push送信
# ----------------------------------------------------
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