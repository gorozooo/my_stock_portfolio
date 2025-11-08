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


# ===== 既定 =====
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


# ===== ユーティリティ =====
def _session_get(request: HttpRequest, key: str, default=None):
    return request.session.get(key, default)

def _session_set(request: HttpRequest, key: str, value):
    request.session[key] = value
    request.session.modified = True

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _nfkc(s: str) -> str:
    try:
        return unicodedata.normalize("NFKC", s or "")
    except Exception:
        return s or ""

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

def _pick_atr_last(feat) -> float:
    try:
        atr_cols = [c for c in feat.columns if "ATR" in c.upper()]
        if atr_cols:
            return float(feat[atr_cols[-1]].iloc[-1])
    except Exception:
        pass
    return 0.0

def _score_to_100(score: float) -> int:
    v = (score + 5.0) / 10.0
    return int(max(0, min(100, round(v * 100))))

def _fmt_reason_lines(reasons: Dict[str, float], last_close: float, atr: float) -> List[str]:
    lines: List[str] = []
    rsi   = reasons.get("rsi")
    macd  = reasons.get("macd_hist")
    vgap  = reasons.get("vwap_gap_pct")
    ret5  = reasons.get("ret5")
    slope = reasons.get("slope")

    if rsi is not None:
        if rsi >= 70: lines.append(f"RSI {rsi:.0f}（強め・過熱気味）")
        elif rsi >= 50: lines.append(f"RSI {rsi:.0f}（50超え＝上向き）")
        else: lines.append(f"RSI {rsi:.0f}（弱め）")

    if macd is not None:
        lines.append(("MACDヒスト +%.3f（買い優勢）" if macd > 0 else "MACDヒスト %.3f（売り優勢）") % macd)

    if vgap is not None:
        lines.append(f"VWAP乖離 {vgap:+.2f}%（行き過ぎ小）" if abs(vgap) < 1.5 else f"VWAP乖離 {vgap:+.2f}%（行き過ぎ注意）")

    if ret5 is not None:
        lines.append(f"直近5日 {ret5:+.2f}%")

    if slope is not None:
        lines.append("短期の傾き：上" if slope > 0 else ("短期の傾き：下" if slope < 0 else "短期の傾き：横"))

    if last_close > 0 and atr > 0:
        vol_pct = atr / last_close * 100
        lines.append(f"ボラ目安 ATR={atr:.1f}円（株価比 {vol_pct:.1f}%）")

    concerns: List[str] = []
    if rsi is not None and rsi > 75: concerns.append("RSI過熱")
    if vgap is not None and vgap > 1.5: concerns.append("短期乖離が大きい")
    if last_close and atr and (atr / last_close) > 0.04: concerns.append("値動きが荒い")
    if concerns: lines.append("懸念：" + "・".join(concerns))
    return lines

def _position_sizing(entry: float, sl: float, last_close: float) -> Tuple[int, float, float]:
    if entry <= 0 or sl <= 0 or entry <= sl:
        return (0, 0.0, 0.0)
    per_share_risk = max(entry - sl, max(1.0, last_close * 0.001))
    allow_risk = max(TOTAL_EQUITY * RISK_PCT, 1.0)
    raw_qty = int(math.floor(allow_risk / per_share_risk))
    qty = (raw_qty // LOT_SIZE) * LOT_SIZE
    if qty > 0:
        if CASH_BUYING_POWER:
            max_cash_qty = int(CASH_BUYING_POWER // entry)
            qty = min(qty, (max_cash_qty // LOT_SIZE) * LOT_SIZE)
        if MARGIN_BUYING_POWER:
            max_margin_qty = int(MARGIN_BUYING_POWER // entry)
            qty = min(qty, (max_margin_qty // LOT_SIZE) * LOT_SIZE)
    required_cash = qty * entry if qty > 0 else 0.0
    est_loss = qty * (entry - sl) if qty > 0 else 0.0
    return qty, required_cash, est_loss

def _entry_tp_sl(last_close: float, atr: float, horizon: str, mode: str) -> Tuple[float, float, float]:
    if last_close <= 0:
        return (0.0, 0.0, 0.0)
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


# ===== 33業種の確実な名称解決 =====
# 候補になりそうなフィールド名を広くカバー（JPXマスタの列が何で入っても拾う）
_SECTOR_NAME_FIELDS = [
    "sector_name", "sector33_name", "industry33_name", "industry_name",
    "sector_jp", "industry_jp", "sector_label", "industry_label",
    "category", "category_name",
]
_SECTOR_CODE_FIELDS = [
    "sector_code", "sector33_code", "industry33_code", "industry_code",
    "sector", "industry", "category_code",
]

# JPX 33業種（代表的なコード文字列 → 日本語名）
# ※ ベンダや資料で "50"/"0050" のような表記ゆれを吸収
_JPX33_MAP = {
    "005": "水産・農林業", "5": "水産・農林業", "0005": "水産・農林業",
    "010": "鉱業", "10": "鉱業", "0010": "鉱業",
    "020": "建設業", "20": "建設業", "0020": "建設業",
    "025": "食料品", "25": "食料品", "0025": "食料品", "50": "食料品", "0050": "食料品",
    "030": "繊維製品", "30": "繊維製品", "0030": "繊維製品",
    "035": "パルプ・紙", "35": "パルプ・紙", "0035": "パルプ・紙",
    "040": "化学", "40": "化学", "0040": "化学",
    "045": "医薬品", "45": "医薬品", "0045": "医薬品", "17": "医薬品", "0017": "医薬品",
    "050": "石油・石炭製品", "0050x": "石油・石炭製品",
    "055": "ゴム製品", "55": "ゴム製品", "0055": "ゴム製品",
    "060": "ガラス・土石製品", "60": "ガラス・土石製品", "0060": "ガラス・土石製品",
    "065": "鉄鋼", "65": "鉄鋼", "0065": "鉄鋼",
    "070": "非鉄金属", "70": "非鉄金属", "0070": "非鉄金属",
    "075": "金属製品", "75": "金属製品", "0075": "金属製品",
    "080": "機械", "80": "機械", "0080": "機械",
    "085": "電気機器", "85": "電気機器", "0085": "電気機器", "65x": "電気機器",
    "090": "輸送用機器", "90": "輸送用機器", "0090": "輸送用機器",
    "095": "精密機器", "95": "精密機器", "0095": "精密機器",
    "100": "その他製品",
    "105": "電気・ガス業",
    "110": "陸運業",
    "115": "海運業",
    "120": "空運業",
    "125": "倉庫・運輸関連業",
    "130": "情報・通信業",
    "135": "卸売業",
    "140": "小売業",
    "145": "銀行業",
    "150": "証券・商品先物取引業",
    "155": "保険業",
    "160": "その他金融業",
    "165": "不動産業",
    "170": "サービス業",
}

# 記号や空白のみの文字列を空扱いにする
_EMPTY_RE = re.compile(r"^[\s・\-–—~＿\.]*$")

def _clean_or_none(s: str) -> str | None:
    if not s:
        return None
    s2 = _nfkc(str(s)).strip()
    if not s2 or _EMPTY_RE.match(s2):
        return None
    return s2

def _resolve_sector(obj: StockMaster) -> str:
    # 1) 名称フィールドを優先
    for f in _SECTOR_NAME_FIELDS:
        if hasattr(obj, f):
            v = _clean_or_none(getattr(obj, f))
            if v:
                return v

    # 2) コードを名称に変換
    for f in _SECTOR_CODE_FIELDS:
        if hasattr(obj, f):
            raw = getattr(obj, f)
            if raw is None:
                continue
            s = _nfkc(str(raw)).strip()
            if not s:
                continue
            # "50", "0050", " 050 " などを吸収
            s_norm = s.zfill(3) if s.isdigit() and len(s) <= 3 else s
            # 3桁・4桁・2桁も順に当ててみる
            cand = (
                _JPX33_MAP.get(s)
                or _JPX33_MAP.get(s_norm)
                or _JPX33_MAP.get(s.zfill(4))
                or _JPX33_MAP.get(s.split(".")[0])  # "50.0" のような表記ゆれ
            )
            if cand:
                return cand

    # 3) 最後の保険：industry/sector文字列があるなら（数字のみは捨てる）
    for f in ("sector", "industry33", "industry", "category"):
        if hasattr(obj, f):
            v = _clean_or_none(getattr(obj, f))
            if v and not v.isdigit():
                return v

    return "—"


# ===== LIVE 構築 =====
def _build_live_items(horizon: str, mode: str, topn: int = TOPN) -> List[Dict]:
    qs = StockMaster.objects.all().order_by("code")[:UNIVERSE_LIMIT]
    got: List[Dict] = []

    for obj in qs:
        code   = getattr(obj, "code", "")
        name   = getattr(obj, "name", "")
        sector = _resolve_sector(obj)

        df = get_prices(code, 180)
        if len(df) < 60:
            continue

        feat   = compute_features(df)
        detail = score_and_detail(feat, mode=mode, horizon=horizon)

        last_close = float(df["Close"].iloc[-1])
        atr        = _pick_atr_last(feat)
        entry, tp, sl = _entry_tp_sl(last_close, atr, horizon, mode)
        qty, required_cash, est_loss = _position_sizing(entry, sl, last_close)
        est_profit = max(qty * (tp - entry), 0.0) if qty > 0 else 0.0

        reasons_text = _fmt_reason_lines(detail.reasons, last_close, atr)

        got.append({
            "code": code,
            "name": name,
            "name_norm": _nfkc(name),
            "sector": sector,
            "score": round(detail.score, 3),
            "score_100": _score_to_100(detail.score),
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


# ===== ビュー =====
def picks(request: HttpRequest) -> HttpResponse:
    if request.GET.get("reset") == "1":
        for k in ["aiapp_mode", "aiapp_horizon", "aiapp_style", "aiapp_tone"]:
            if k in request.session:
                del request.session[k]

    mode = request.GET.get("mode") or _session_get(request, "aiapp_mode", "live")
    if mode not in ("live", "demo"): mode = "live"
    _session_set(request, "aiapp_mode", mode)

    horizon = request.GET.get("horizon") or _session_get(request, "aiapp_horizon", DEFAULT_HORIZON)
    if horizon not in ("short", "mid", "long"): horizon = DEFAULT_HORIZON
    _session_set(request, "aiapp_horizon", horizon)

    style = request.GET.get("style") or _session_get(request, "aiapp_style", DEFAULT_MODE)
    if style not in ("aggressive", "normal", "defensive"): style = DEFAULT_MODE
    _session_set(request, "aiapp_style", style)

    tone = request.GET.get("tone") or _session_get(request, "aiapp_tone", DEFAULT_TONE)
    _session_set(request, "aiapp_tone", tone)

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
        "risk_pct": RISK_PCT,
        "lot_size": LOT_SIZE,
    }
    return render(request, "aiapp/picks.html", context)