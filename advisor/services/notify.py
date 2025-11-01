# advisor/services/notify.py
from __future__ import annotations
import os, json, requests
from typing import Dict, Any, Optional, List
from django.conf import settings

from portfolio.utils.line_client import push_text, push_flex

# ---- （任意）厳密TP/SL計算に利用。無ければフォールバック ----
try:
    from advisor.services.policy_rules import compute_exit_targets
except Exception:
    compute_exit_targets = None  # type: ignore

# ---- JPX銘柄マップ（data/tse_list.json）----
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

def _txt(text, *, size="sm", weight=None, color="#e5e7eb", align="start", wrap=True):
    node = {"type":"text","text":str(text), "size":size, "color":color, "wrap":wrap}
    if weight: node["weight"]=weight
    if align: node["align"]=align
    return node

def _pb(label, data, style="primary"):
    return {"type":"button","style":style,"height":"sm",
            "action":{"type":"postback","label":label,"data":data}}

def _kpi_cell(label: str, value: str) -> Dict[str, Any]:
    return {
        "type":"box", "layout":"vertical", "flex":1, "spacing":"xs",
        "contents":[
            _txt(value, size="lg", weight="bold", color="#f8fafc", align="center", wrap=True),
            _txt(label, size="xs", color="#94a3b8", align="center", wrap=True)
        ]
    }


# ------------------------------------------------------
# Flexバブル生成（日本語・省略抑制・視線誘導調整済）
# ------------------------------------------------------
def _build_trade_bubble(*, window: str, ticker: str, name: Optional[str],
                        score: Optional[int], weekly: str, slope_yr: Optional[float],
                        theme: Optional[float],
                        entry_price: Optional[int],
                        tp_price: Optional[int], sl_price: Optional[int],
                        policy_line: str) -> Dict[str, Any]:
    title = f"[{window}] {_display_ticker(ticker)}"
    jp_name = _jpx_name(ticker, name)

    header = {
        "type":"box","layout":"vertical","paddingAll":"16px","spacing":"xs",
        "contents":[
            _txt(title, size="xs", color="#93c5fd", wrap=True),
            _txt(jp_name, size="xl", weight="bold", color="#f8fafc", wrap=True),
        ]
    }

    # 2行×2列のKPIグリッド（省略を抑制）
    kpi_row1 = {
        "type":"box","layout":"horizontal","spacing":"sm","contents":[
            _kpi_cell("スコア", str(score if score is not None else "—")),
            _kpi_cell("週次", {"up":"上昇","down":"下落"}.get(weekly, weekly or "—")),
        ]
    }
    kpi_row2 = {
        "type":"box","layout":"horizontal","spacing":"sm","contents":[
            _kpi_cell("傾き", f"{round((slope_yr or 0.0)*100,1)}%/yr"),
            _kpi_cell("テーマ", str(int(round((theme or 0.0)*100)))),
        ]
    }
    kpi = {
        "type":"box","layout":"vertical","paddingAll":"8px","spacing":"sm",
        "contents":[kpi_row1, kpi_row2]
    }

    policies = {
        "type":"box","layout":"vertical","paddingAll":"12px","spacing":"xs",
        "contents":[
            _txt("採用ポリシー", size="xs", color="#94a3b8", wrap=True),
            _txt(policy_line or "—", size="sm", color="#e5e7eb", wrap=True),
        ]
    }

    prices = {
        "type":"box","layout":"vertical","paddingAll":"12px","spacing":"sm",
        "contents":[
            {"type":"box","layout":"vertical","spacing":"xs","contents":[
                _txt("参考エントリー", size="xs", color="#94a3b8", wrap=True),
                _txt(_yen(entry_price), size="md", weight="bold", color="#f8fafc"),
            ]},
            {"type":"box","layout":"horizontal","spacing":"sm","contents":[
                {"type":"box","layout":"vertical","flex":1,"spacing":"xs","contents":[
                    _txt("目標 (TP)", size="xs", color="#94a3b8", wrap=True),
                    _txt(_yen(tp_price), size="md", weight="bold", color="#22c55e"),
                ]},
                {"type":"box","layout":"vertical","flex":1,"spacing":"xs","contents":[
                    _txt("損切り (SL)", size="xs", color="#94a3b8", wrap=True),
                    _txt(_yen(sl_price), size="md", weight="bold", color="#ef4444"),
                ]},
            ]},
        ]
    }

    body = {
        "type":"box","layout":"vertical","backgroundColor":"#0b0f1a",
        "contents":[
            header,
            {"type":"separator","color":"#1f2937"},
            kpi,
            {"type":"separator","color":"#1f2937"},
            policies,
            {"type":"separator","color":"#1f2937"},
            prices,
        ]
    }

    footer = {
        "type":"box","layout":"vertical","spacing":"sm","paddingAll":"14px",
        "contents":[
            _pb("発注メモに保存", f"save:{_display_ticker(ticker)}"),
            _pb("2時間後に再通知", f"snooze:{_display_ticker(ticker)}:120", style="secondary"),
            _pb("今回は見送り", f"reject:{_display_ticker(ticker)}", style="secondary"),
        ]
    }

    return {
        "type":"bubble","size":"giga",
        "styles":{"body":{"backgroundColor":"#0b0f1a"}, "footer":{"backgroundColor":"#0b0f1a"}},
        "body": body,
        "footer": footer
    }

# ------------------------------------------------------
# TrendResult → Flex（exits: tp/sl を優先採用）
# ------------------------------------------------------
def make_flex_from_tr(tr_obj: Any, policies: List[str], *, window: str,
                      exits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = None
    try:
        entry = int(tr_obj.entry_price_hint or tr_obj.close_price or 0) or None
    except Exception:
        entry = None

    tp_price = (exits or {}).get("tp_price")
    sl_price = (exits or {}).get("sl_price")

    if (tp_price is None or sl_price is None) and entry:
        if compute_exit_targets is not None:
            try:
                xt = compute_exit_targets(
                    policy={"targets":{}, "exits":{}},
                    ticker=str(tr_obj.ticker).upper(),
                    entry_price=entry,
                    days_held=None,
                    atr14_hint=(getattr(tr_obj, "notes", {}) or {}).get("atr14"),
                )
                tp_price = tp_price or xt.tp_price
                sl_price = sl_price or xt.sl_price
            except Exception:
                pass
        if tp_price is None: tp_price = int(round(entry * 1.06))
        if sl_price is None: sl_price = int(round(entry * 0.98))

    policy_line = " / ".join(policies) if policies else "—"

    return _build_trade_bubble(
        window=window,
        ticker=str(tr_obj.ticker),
        name=(getattr(tr_obj, "name", None) or None),
        score=int(tr_obj.overall_score or 0) if tr_obj.overall_score is not None else None,
        weekly=str(tr_obj.weekly_trend or "—"),
        slope_yr=float(tr_obj.slope_annual or 0.0) if tr_obj.slope_annual is not None else None,
        theme=float(tr_obj.theme_score or 0.0) if tr_obj.theme_score is not None else None,
        entry_price=entry,
        tp_price=tp_price, sl_price=sl_price,
        policy_line=policy_line,
    )

# ------------------------------------------------------
# LINE push（alt_text/text/flexで呼ぶ）
# ------------------------------------------------------
def push_line_message(*, alt_text: str, text: Optional[str] = None, flex: Optional[Dict[str, Any]] = None) -> None:
    """
    Advisor から LINE 送信。複数ユーザーにも対応（カンマ区切り）
    """
    uids = [u.strip() for u in os.getenv("LINE_TO_USER_IDS", "").split(",") if u.strip()]
    if not uids:
        print("[LINE diag] no recipients"); return

    if text is None and flex is None:
        print("[LINE diag] skip (no text/flex)"); return

    for uid in uids:
        try:
            if flex is not None:
                r = push_flex(uid, alt_text=alt_text, contents=flex, quick_reply=True)
            else:
                r = push_text(uid, text or alt_text)
            print("[LINE push]", uid, r.status_code, r.text[:200])
        except Exception as e:
            print("[LINE push error]", uid, e)