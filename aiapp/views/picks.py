# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from datetime import datetime
from typing import Dict, List, Tuple

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_and_detail


DEFAULT_HORIZON = "short"
DEFAULT_MODE    = "aggressive"
DEFAULT_TONE    = "calm"

UNIVERSE_LIMIT  = int(getattr(settings, "AIAPP_UNIVERSE_LIMIT", 400))
SNAPSHOT_DIR    = getattr(settings, "AIAPP_SNAPSHOT_DIR", "media/aiapp/picks")
SAVE_SNAPSHOT   = bool(getattr(settings, "AIAPP_SAVE_SNAPSHOT", False))

RISK_PCT            = float(getattr(settings, "AIAPP_RISK_PCT", 0.02))
TOTAL_EQUITY        = float(getattr(settings, "AIAPP_TOTAL_EQUITY", 1_000_000))
CASH_BUYING_POWER   = getattr(settings, "AIAPP_CASH_BUYING_POWER", None)
MARGIN_BUYING_POWER = getattr(settings, "AIAPP_MARGIN_BUYING_POWER", None)
LOT_SIZE            = int(getattr(settings, "AIAPP_LOT_SIZE", 100))

TOPN = 10


def _nfkc(s: str) -> str:
    try:
        return unicodedata.normalize("NFKC", s or "")
    except Exception:
        return s or ""


# ---- セクター名解決（後方互換含む） ----------------------------
_SECTOR_NAME_FIELDS = [
    "sector_name", "sector33_name", "industry33_name", "industry_name",
    "sector_jp", "industry_jp", "sector_label", "industry_label",
    "category", "category_name",
    "sector33",   # 旧環境の単一列に対応
]
_SECTOR_CODE_FIELDS = [
    "sector_code", "sector33_code", "industry33_code", "industry_code",
    "sector", "industry", "category_code", "sector33",
]

_EMPTY_RE = re.compile(r"^[\s・\-–—~＿\.]*$")

def _clean_or_none(s: str) -> str | None:
    if not s:
        return None
    s2 = _nfkc(str(s)).strip()
    if not s2 or _EMPTY_RE.match(s2):
        return None
    return s2

def _resolve_sector(obj: StockMaster) -> str:
    for f in _SECTOR_NAME_FIELDS:
        if hasattr(obj, f):
            v = _clean_or_none(getattr(obj, f))
            if v and not v.isdigit():
                return v
    for f in _SECTOR_CODE_FIELDS:
        if hasattr(obj, f):
            raw = getattr(obj, f)
            if raw is None:
                continue
            s = _nfkc(str(raw)).strip()
            if not s:
                continue
            s3 = (s[-3:] if s.isdigit() and len(s) >= 3 else s.zfill(3)) if s.isdigit() else s
            from aiapp.services.fetch_master import JPX33_MAP
            name = JPX33_MAP.get(s3) or JPX33_MAP.get(s) or JPX33_MAP.get(s.zfill(3))
            if name:
                return name
    for f in ("sector", "industry33", "industry", "category"):
        if hasattr(obj, f):
            v = _clean_or_none(getattr(obj, f))
            if v and not v.isdigit():
                return v
    return "—"


# ---- スコア→100点化、ATR, 位置サイズ等 ------------------------
def _score_to_100(score: float) -> int:
    v = (score + 5.0) / 10.0
    return int(max(0, min(100, round(v * 100))))

def _pick_atr_last(feat) -> float:
    try:
        cols = [c for c in feat.columns if "ATR" in c.upper()]
        if cols: return float(feat[cols[-1]].iloc[-1])
    except Exception:
        pass
    return 0.0

def _entry_tp_sl(last_close: float, atr: float, horizon: str, mode: str):
    if last_close <= 0: return (0.0, 0.0, 0.0)
    if horizon == "short":
        if mode == "aggressive": tp_k, sl_k, ent_k = 1.5, 1.0, 0.25
        elif mode == "defensive": tp_k, sl_k, ent_k = 1.0, 0.8, 0.10
        else: tp_k, sl_k, ent_k = 1.2, 0.9, 0.15
    elif horizon == "mid":
        if mode == "aggressive": tp_k, sl_k, ent_k = 2.0, 1.2, 0.35
        elif mode == "defensive": tp_k, sl_k, ent_k = 1.4, 1.0, 0.15
        else: tp_k, sl_k, ent_k = 1.7, 1.1, 0.25
    else:
        if mode == "aggressive": tp_k, sl_k, ent_k = 3.0, 1.6, 0.50
        elif mode == "defensive": tp_k, sl_k, ent_k = 2.0, 1.2, 0.20
        else: tp_k, sl_k, ent_k = 2.5, 1.4, 0.30
    vol = atr / last_close if (last_close > 0 and atr > 0) else 0.01
    entry = last_close * (1 - ent_k * vol)
    tp    = last_close * (1 + tp_k  * vol)
    sl    = last_close * (1 - sl_k  * vol)
    return entry, tp, sl

def _position_sizing(entry: float, sl: float, last_close: float):
    if entry <= 0 or sl <= 0 or entry <= sl: return (0, 0.0, 0.0)
    per_share_risk = max(entry - sl, max(1.0, last_close * 0.001))
    allow_risk = max(TOTAL_EQUITY * RISK_PCT, 1.0)
    raw_qty = int(allow_risk // per_share_risk)
    qty = (raw_qty // LOT_SIZE) * LOT_SIZE
    if qty > 0:
        if CASH_BUYING_POWER:
            max_cash_qty = int(CASH_BUYING_POWER // entry)
            qty = min(qty, (max_cash_qty // LOT_SIZE) * LOT_SIZE)
        if MARGIN_BUYING_POWER:
            max_margin_qty = int(MARGIN_BUYING_POWER // entry)
            qty = min(qty, (max_margin_qty // LOT_SIZE) * LOT_SIZE)
    required = qty * entry if qty > 0 else 0.0
    est_loss = qty * (entry - sl) if qty > 0 else 0.0
    return qty, required, est_loss

def _fmt_reason_lines(reasons: Dict[str, float], last_close: float, atr: float) -> List[str]:
    lines: List[str] = []
    rsi   = reasons.get("rsi"); macd  = reasons.get("macd_hist")
    vgap  = reasons.get("vwap_gap_pct"); ret5  = reasons.get("ret5")
    slope = reasons.get("slope")
    if rsi is not None:
        if rsi >= 70: lines.append(f"RSI {rsi:.0f}（強め・過熱気味）")
        elif rsi >= 50: lines.append(f"RSI {rsi:.0f}（50超え＝上向き）")
        else: lines.append(f"RSI {rsi:.0f}（弱め）")
    if macd is not None:
        lines.append(("MACDヒスト +%.3f（買い優勢）" if macd > 0 else "MACDヒスト %.3f（売り優勢）") % macd)
    if vgap is not None:
        lines.append(f"VWAP乖離 {vgap:+.2f}%（行き過ぎ小）" if abs(vgap) < 1.5 else f"VWAP乖離 {vgap:+.2f}%（行き過ぎ注意）")
    if ret5 is not None: lines.append(f"直近5日 {ret5:+.2f}%")
    if slope is not None: lines.append("短期の傾き：上" if slope > 0 else ("短期の傾き：下" if slope < 0 else "短期の傾き：横"))
    if last_close > 0 and atr > 0:
        vol_pct = atr / last_close * 100; lines.append(f"ボラ目安 ATR={atr:.1f}円（株価比 {vol_pct:.1f}%）")
    concerns: List[str] = []
    if rsi is not None and rsi > 75: concerns.append("RSI過熱")
    if vgap is not None and vgap > 1.5: concerns.append("短期乖離が大きい")
    if last_close and atr and (atr / last_close) > 0.04: concerns.append("値動きが荒い")
    if concerns: lines.append("懸念：" + "・".join(concerns))
    return lines


def _build_live_items(horizon: str, mode: str, topn: int):
    qs = StockMaster.objects.all().order_by("code")[:int(getattr(settings, "AIAPP_UNIVERSE_LIMIT", 400))]
    got: List[Dict] = []
    for obj in qs:
        code = getattr(obj, "code", ""); name = getattr(obj, "name", "")
        sector = _resolve_sector(obj)
        df = get_prices(code, 180)
        if len(df) < 60: continue
        feat = compute_features(df)
        detail = score_and_detail(feat, mode=mode, horizon=horizon)
        last_close = float(df["Close"].iloc[-1]); atr = _pick_atr_last(feat)
        entry, tp, sl = _entry_tp_sl(last_close, atr, horizon, mode)
        qty, required, est_loss = _position_sizing(entry, sl, last_close)
        est_profit = max(qty * (tp - entry), 0.0) if qty > 0 else 0.0
        got.append({
            "code": code, "name": name, "name_norm": _nfkc(name),
            "sector": sector,
            "score": round(detail.score, 3), "score_100": _score_to_100(detail.score),
            "stars": int(detail.stars), "rules_hit": int(detail.rules_hit),
            "last_close": last_close, "entry": entry, "tp": tp, "sl": sl,
            "qty": qty, "required_cash": required, "est_pl": est_profit, "est_loss": est_loss,
            "reasons_text": _fmt_reason_lines(detail.reasons, last_close, atr),
        })
    got.sort(key=lambda x: x["score"], reverse=True)
    return got[:topn]


def picks(request: HttpRequest) -> HttpResponse:
    if request.GET.get("reset") == "1":
        for k in ["aiapp_mode", "aiapp_horizon", "aiapp_style", "aiapp_tone"]:
            if k in request.session: del request.session[k]

    mode = request.GET.get("mode") or request.session.get("aiapp_mode", "live")
    if mode not in ("live", "demo"): mode = "live"
    request.session["aiapp_mode"] = mode

    horizon = request.GET.get("horizon") or request.session.get("aiapp_horizon", DEFAULT_HORIZON)
    if horizon not in ("short", "mid", "long"): horizon = DEFAULT_HORIZON
    request.session["aiapp_horizon"] = horizon

    style = request.GET.get("style") or request.session.get("aiapp_style", DEFAULT_MODE)
    if style not in ("aggressive", "normal", "defensive"): style = DEFAULT_MODE
    request.session["aiapp_style"] = style

    tone = request.GET.get("tone") or request.session.get("aiapp_tone", DEFAULT_TONE)
    request.session["aiapp_tone"] = tone

    is_demo = (mode == "demo")
    if is_demo:
        items = []
    else:
        items = _build_live_items(horizon=horizon, mode=style, topn=TOPN)

    updated_at = datetime.now()
    ctx = {
        "updated_label": updated_at.strftime("%Y/%m/%d(%a) %H:%M"),
        "mode_label": "DEMO" if is_demo else "LIVE",
        "is_demo": is_demo,
        "items": items,
        "horizon": horizon, "style": style, "tone": tone,
        "risk_pct": getattr(settings, "AIAPP_RISK_PCT", 0.02),
        "lot_size": getattr(settings, "AIAPP_LOT_SIZE", 100),
    }
    return render(request, "aiapp/picks.html", ctx)