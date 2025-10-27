# advisor/services/board_source.py
from __future__ import annotations
from typing import Dict, Any, List, Tuple
from datetime import datetime, timezone, timedelta
from django.conf import settings

from .quotes import get_last_price, get_week_trend_hint

JST = timezone(timedelta(hours=9))

# ---- 仕様：ハイライト候補（銘柄群）----
# 実運用化する時は、ここをモデル出力／スクリーナー結果に差し替え。
_BASE_ITEMS: List[Dict[str, Any]] = [
    {
        "ticker": "8035.T",
        "name": "東京エレクトロン",
        "segment": "中期（20〜45日）",
        "action": "買い候補（勢い強）",
        "reasons": ["半導体テーマが強い（78点）", "出来高が増えている（+35%）", "あなたの得意型（AI勝率82%）"],
        "ai": {"win_prob": 0.82, "size_mult": 1.08},
        "theme": {"id": "semiconductor", "label": "半導体", "score": 0.78},
    },
    {
        "ticker": "7203.T",
        "name": "トヨタ",
        "segment": "中期（20〜45日）",
        "action": "30日目 → 一部売り",
        "reasons": ["保有日数の区切り", "自動車テーマ 65点", "最近は横ばい"],
        "ai": {"win_prob": 0.64, "size_mult": 0.96},
        "theme": {"id": "auto", "label": "自動車", "score": 0.65},
    },
    {
        "ticker": "6758.T",
        "name": "ソニーG",
        "segment": "短期（5〜10日）",
        "action": "買い候補（短期の勢い）",
        "reasons": ["出来高が増えている", "戻りが強い", "AI勝率74%"],
        "ai": {"win_prob": 0.74, "size_mult": 1.05},
        "theme": {"id": "electronics", "label": "電機", "score": 0.58},
    },
    {
        "ticker": "8267.T",
        "name": "イオン",
        "segment": "NISA（長期）",
        "action": "配当・優待目的で継続",
        "reasons": ["決算前の確認", "生活必需で安定", "分散の役割"],
        "ai": {"win_prob": 0.60, "size_mult": 1.00},
        "theme": {"id": "retail", "label": "小売", "score": 0.55},
    },
    {
        "ticker": "8306.T",
        "name": "三菱UFJ",
        "segment": "中期（20〜45日）",
        "action": "買い候補（銀行）",
        "reasons": ["銀行テーマ 41点（様子見寄り）", "値動きは安定", "分散の候補"],
        "ai": {"win_prob": 0.61, "size_mult": 0.92},
        "theme": {"id": "banks", "label": "銀行", "score": 0.41},
    },
]

def _tp_sl_pct(segment: str) -> Tuple[float, float]:
    s = segment or ""
    if "短期" in s: return 0.06, 0.02   # +6%/-2%
    if "中期" in s: return 0.10, 0.03   # +10%/-3%
    return 0.12, 0.05                   # 長期/NISA

def _weekly_trend(theme_score: float, win_prob: float, hint: str|None) -> str:
    if hint in ("up","flat","down"): return hint
    score = 0.7*win_prob + 0.3*theme_score
    if score >= 0.62: return "up"
    if score >= 0.48: return "flat"
    return "down"

def _overall(theme_score: float, win_prob: float) -> int:
    return int(round((0.7*win_prob + 0.3*theme_score)*100))

def _tp_sl_prob(win_prob: float) -> Tuple[float,float]:
    tp = max(0.0, min(1.0, win_prob*0.46))
    sl = max(0.0, min(1.0, (1.0-win_prob)*0.30))
    return tp, sl

def build_board(user) -> Dict[str, Any]:
    """
    API /advisor/api/board/ が返すJSONを構築。
    settings.ADVISOR_LIVE が真なら quotes.get_last_price() を“優先”。
    取れなければ静的フォールバック。
    """
    jst_now = datetime.now(JST)

    # （デモ）資金前提。将来はUser設定/口座APIへ。
    credit_balance = 1_000_000
    risk_per_trade = 0.01

    highlights: List[Dict[str, Any]] = []
    for it in _BASE_ITEMS:
        tkr = it["ticker"]

        # 実データ or フォールバック
        px = get_last_price(tkr)
        if px is None:
            # 実データが無ければ“デモ価格”で破綻させない
            px = 3000

        tp_pct, sl_pct = _tp_sl_pct(it["segment"])
        tp_price = int(round(px*(1+tp_pct)))
        sl_price = int(round(px*(1-sl_pct)))

        win_prob   = float(it["ai"]["win_prob"])
        theme_score= float(it["theme"]["score"])
        wk_hint    = get_week_trend_hint(tkr)
        weekly     = _weekly_trend(theme_score, win_prob, wk_hint)
        overall    = _overall(theme_score, win_prob)
        tp_prob, sl_prob = _tp_sl_prob(win_prob)
        # 簡易ポジションサイズ
        stop_val = max(1, px - sl_price)
        shares   = max(0, int((credit_balance*risk_per_trade)//stop_val))
        need_cash= shares*px if shares>0 else None

        highlights.append({
            **it,
            "weekly_trend": weekly,
            "overall_score": overall,
            "entry_price_hint": px,
            "targets": {
                "tp": it.get("targets",{}).get("tp", f"目標 +{int(tp_pct*100)}%"),
                "sl": it.get("targets",{}).get("sl", f"損切り -{int(sl_pct*100)}%"),
                "tp_pct": tp_pct, "sl_pct": sl_pct,
                "tp_price": tp_price, "sl_price": sl_price,
            },
            "sizing": {
                "credit_balance": credit_balance,
                "risk_per_trade": risk_per_trade,
                "position_size_hint": shares or None,
                "need_cash": need_cash,
            },
            "ai": { **it["ai"], "tp_prob": tp_prob, "sl_prob": sl_prob },
        })

    data: Dict[str, Any] = {
        "meta": {
            "generated_at": jst_now.replace(hour=7, minute=25, second=0, microsecond=0).isoformat(),
            "model_version": "v0.2-board-source",
            "adherence_week": 0.84,
            "regime": {"trend_prob": 0.63, "range_prob": 0.37, "nikkei": "↑", "topix": "→"},
            "scenario": "半導体に資金回帰。短期は押し目継続、週足↑",
            "pairing": {"id": 2, "label": "順張り・短中期"},
            "self_mirror": {"recent_drift": "損切り未実施 3/4件"},
            "credit_balance": credit_balance,
            "live": bool(getattr(settings, "ADVISOR_LIVE", False)),
        },
        "theme": {
            "week": "2025-W43",
            "top3": [
                {"id":"semiconductor","label":"半導体","score":0.78},
                {"id":"travel","label":"旅行","score":0.62},
                {"id":"banks","label":"銀行","score":0.41},
            ],
        },
        "highlights": highlights[:5],
    }
    return data