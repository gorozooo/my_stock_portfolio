# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_and_detail

# ===== 既定（ユーザー指定がなければ短期×攻め×淡々） =====
DEFAULT_HORIZON = "short"        # short / mid / long
DEFAULT_MODE    = "aggressive"   # aggressive / normal / defensive
DEFAULT_TONE    = "calm"         # calm(淡々) / neutral / positive

# ユニバース最大件数（重すぎ防止）
UNIVERSE_LIMIT = int(getattr(settings, "AIAPP_UNIVERSE_LIMIT", 400))

# スナップショット（任意保存）。無ければオンデマンドで計算して表示
SNAPSHOT_DIR = getattr(settings, "AIAPP_SNAPSHOT_DIR", "media/aiapp/picks")
SAVE_SNAPSHOT = bool(getattr(settings, "AIAPP_SAVE_SNAPSHOT", False))

# リスク・資金まわり（既定）。本番では settings.py で上書きしてOK。
RISK_PCT = float(getattr(settings, "AIAPP_RISK_PCT", 0.02))   # 許容リスク=総資産×2%
TOTAL_EQUITY = float(getattr(settings, "AIAPP_TOTAL_EQUITY", 1_000_000.0))  # 総資産の既定
CASH_BUYING_POWER = getattr(settings, "AIAPP_CASH_BUYING_POWER", None)      # 現物買付可能額（任意）
MARGIN_BUYING_POWER = getattr(settings, "AIAPP_MARGIN_BUYING_POWER", None)  # 信用余力（任意）

TOPN = 10  # 表示件数


# ====== 小ユーティリティ ======

def _session_get(request: HttpRequest, key: str, default=None):
    return request.session.get(key, default)

def _session_set(request: HttpRequest, key: str, value):
    request.session[key] = value
    request.session.modified = True

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _load_snapshot_latest(kind: str = "live") -> List[Dict]:
    try:
        _ensure_dir(SNAPSHOT_DIR)
        files = sorted(
            [os.path.join(SNAPSHOT_DIR, f) for f in os.listdir(SNAPSHOT_DIR) if f.endswith(f"_{kind}.json")],
            key=lambda p: os.path.getmtime(p),
        )
        if not files:
            return []
        with open(files[-1], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_snapshot(items: List[Dict], kind: str = "live"):
    if not SAVE_SNAPSHOT:
        return
    _ensure_dir(SNAPSHOT_DIR)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SNAPSHOT_DIR, f"{ts}_{kind}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
    except Exception:
        pass

def _nfkc(s: str) -> str:
    try:
        return unicodedata.normalize("NFKC", s or "")
    except Exception:
        return s or ""

def _pick_atr_last(feat) -> float:
    """特徴量からATR系の最新値を安全に取得。なければ0."""
    try:
        # ATR列を優先的に探す
        atr_cols = [c for c in feat.columns if "ATR" in c.upper()]
        if atr_cols:
            return float(feat[atr_cols[-1]].iloc[-1])
    except Exception:
        pass
    return 0.0

def _fmt_reason_lines(reasons: Dict[str, float], feat_last: Dict[str, float]) -> List[str]:
    """数値理由をテキスト化 + 懸念があれば最後に追加。"""
    lines: List[str] = []
    rsi = reasons.get("rsi")
    macd = reasons.get("macd_hist")
    vgap = reasons.get("vwap_gap_pct")
    ret5 = reasons.get("ret5")
    slope = reasons.get("slope")

    if rsi is not None:
        if rsi >= 70:
            lines.append(f"RSI {rsi:.0f}：強め（過熱気味）")
        elif rsi >= 50:
            lines.append(f"RSI {rsi:.0f}：中立～強め")
        else:
            lines.append(f"RSI {rsi:.0f}：弱め")

    if macd is not None:
        if macd > 0:
            lines.append(f"MACDヒストグラム 正領域（{macd:.3f}）")
        else:
            lines.append(f"MACDヒストグラム 負領域（{macd:.3f}）")

    if vgap is not None:
        lines.append(f"VWAP乖離 {vgap:+.2f}%")

    if ret5 is not None:
        lines.append(f"直近5日リターン {ret5:+.2f}%")

    if slope is not None:
        if slope > 0:
            lines.append("短期の傾き：上方向")
        elif slope < 0:
            lines.append("短期の傾き：下方向")
        else:
            lines.append("短期の傾き：フラット")

    # 懸念自動付与
    concerns: List[str] = []
    last_close = feat_last.get("Close", 0.0)
    atr = feat_last.get("ATR", 0.0)
    if rsi is not None and rsi > 75:
        concerns.append("RSI過熱")
    if vgap is not None and vgap > 1.5:
        concerns.append("短期乖離が大きい")
    if last_close and atr and (atr / last_close) > 0.04:
        concerns.append("変動が荒い")

    if concerns:
        lines.append("懸念：" + "・".join(concerns))
    return lines

def _position_sizing(entry: float, sl: float, last_close: float) -> Tuple[int, float, float]:
    """
    リスク基準で株数を決定。
    - 許容リスク金額 = 総資産 × RISK_PCT
    - 1株リスク = max(entry - sl, 最低1ティック相当)
    - 株数 = floor(許容リスク金額 / 1株リスク)
    - 買付余力（現物/信用）が与えられていれば、その上限でクリップ
    """
    if entry <= 0 or sl <= 0 or entry <= sl:
        return (0, 0.0, 0.0)

    per_share_risk = max(entry - sl, max(1.0, last_close * 0.001))  # 1円 or 0.1% のいずれか
    allow_risk = max(TOTAL_EQUITY * RISK_PCT, 1.0)
    qty = int(math.floor(allow_risk / per_share_risk))

    # 現物/信用の上限（任意設定。未設定なら無制限扱い）
    if qty > 0:
        required_cash = qty * entry
        if CASH_BUYING_POWER:
            max_cash_qty = int(CASH_BUYING_POWER // entry)
            qty = min(qty, max_cash_qty)
        if MARGIN_BUYING_POWER:
            max_margin_qty = int(MARGIN_BUYING_POWER // entry)
            qty = min(qty, max_margin_qty)
        required_cash = qty * entry
    else:
        required_cash = 0.0

    est_profit = max(qty * (entry * 1.015 - entry), 0.0)  # TPが無い場合のダミー(後で上書き)
    est_loss = qty * (entry - sl) if qty > 0 else 0.0

    return qty, required_cash, est_loss

def _entry_tp_sl(last_close: float, atr: float, horizon: str, mode: str) -> Tuple[float, float, float]:
    """
    Entry/TP/SLの既定。短期×攻めは TP=+1.5ATR, SL=-1.0ATR。
    中期/長期や守りは穏やかに。
    """
    if last_close <= 0:
        return (0.0, 0.0, 0.0)

    # 係数（適度にメリハリ）
    if horizon == "short":
        if mode == "aggressive":
            tp_k, sl_k = 1.5, 1.0
        elif mode == "defensive":
            tp_k, sl_k = 1.0, 0.8
        else:
            tp_k, sl_k = 1.2, 0.9
    elif horizon == "mid":
        if mode == "aggressive":
            tp_k, sl_k = 2.0, 1.2
        elif mode == "defensive":
            tp_k, sl_k = 1.4, 1.0
        else:
            tp_k, sl_k = 1.7, 1.1
    else:  # long
        if mode == "aggressive":
            tp_k, sl_k = 3.0, 1.6
        elif mode == "defensive":
            tp_k, sl_k = 2.0, 1.2
        else:
            tp_k, sl_k = 2.5, 1.4

    vol = atr / last_close if (last_close > 0 and atr > 0) else 0.01  # 最低1%相当
    entry = last_close
    tp = last_close * (1 + tp_k * vol)
    sl = last_close * (1 - sl_k * vol)
    return entry, tp, sl


# ====== オンデマンド構築 ======

def _build_live_items(horizon: str, mode: str, topn: int = TOPN) -> List[Dict]:
    qs = StockMaster.objects.all().order_by("code").values("code", "name")[:UNIVERSE_LIMIT]
    got: List[Dict] = []

    for row in qs:
        code, name = row["code"], row["name"]
        df = get_prices(code, 180)
        if len(df) < 60:
            continue

        feat = compute_features(df)
        detail = score_and_detail(feat, mode=mode, horizon=horizon)

        last_close = float(df["Close"].iloc[-1])
        atr = _pick_atr_last(feat)
        entry, tp, sl = _entry_tp_sl(last_close, atr, horizon, mode)
        qty, required_cash, est_loss = _position_sizing(entry, sl, last_close)
        est_profit = max(qty * (tp - entry), 0.0) if qty > 0 else 0.0

        feat_last = {
            "Close": last_close,
            "ATR": atr,
        }
        reasons_text = _fmt_reason_lines(detail.reasons, feat_last)

        got.append({
            "code": code,
            "name": name,
            "name_norm": _nfkc(name),
            "score": round(detail.score, 3),
            "stars": int(detail.stars),
            "rules_hit": int(detail.rules_hit),

            "last_close": last_close,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "qty": qty,
            "required_cash": required_cash,
            "est_pl": est_profit,
            "est_loss": est_loss,

            "reasons_text": reasons_text,
        })

    got.sort(key=lambda x: x["score"], reverse=True)
    return got[:topn]


# ====== ビュー本体 ======

def picks(request: HttpRequest) -> HttpResponse:
    """
    /aiapp/picks/
    - ?mode=live|demo （既定 live）
    - ?reset=1        （セッション初期化）
    - ?horizon=short|mid|long
    - ?style=aggressive|normal|defensive
    """
    # セッション初期化
    if request.GET.get("reset") == "1":
        for k in ["aiapp_mode", "aiapp_horizon", "aiapp_style", "aiapp_tone"]:
            if k in request.session:
                del request.session[k]

    # モード
    mode = request.GET.get("mode") or _session_get(request, "aiapp_mode", "live")
    if mode not in ("live", "demo"):
        mode = "live"
    _session_set(request, "aiapp_mode", mode)

    # 期間・スタイル・トーン
    horizon = request.GET.get("horizon") or _session_get(request, "aiapp_horizon", DEFAULT_HORIZON)
    if horizon not in ("short", "mid", "long"):
        horizon = DEFAULT_HORIZON
    _session_set(request, "aiapp_horizon", horizon)

    style = request.GET.get("style") or _session_get(request, "aiapp_style", DEFAULT_MODE)
    if style not in ("aggressive", "normal", "defensive"):
        style = DEFAULT_MODE
    _session_set(request, "aiapp_style", style)

    tone = request.GET.get("tone") or _session_get(request, "aiapp_tone", DEFAULT_TONE)
    _session_set(request, "aiapp_tone", tone)

    # データ取得（DEMO指示時だけデモを読む。無いときは空。LIVEは必ずオンデマンドで出す）
    is_demo = (mode == "demo")
    if is_demo:
        items = _load_snapshot_latest("demo")
    else:
        items = _load_snapshot_latest("live")
        if not items:
            items = _build_live_items(horizon=horizon, mode=style, topn=TOPN)
            _save_snapshot(items, "live")

    updated_at = datetime.now()
    context = {
        "updated_label": updated_at.strftime("%Y/%m/%d(%a) %H:%M"),
        "mode_label": "DEMO" if is_demo else "LIVE",
        "is_demo": is_demo,
        "items": items,
        "horizon": horizon,
        "style": style,
        "tone": tone,
    }
    return render(request, "aiapp/picks.html", context)