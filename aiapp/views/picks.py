# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import os
import unicodedata
from datetime import datetime
from typing import Dict, List, Tuple, Optional

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
DEFAULT_TONE    = "calm"         # calm / neutral / positive

# ユニバース最大件数（重すぎ防止）
UNIVERSE_LIMIT = int(getattr(settings, "AIAPP_UNIVERSE_LIMIT", 400))

# スナップショット（任意保存）。無ければオンデマンドで計算して表示
SNAPSHOT_DIR   = getattr(settings, "AIAPP_SNAPSHOT_DIR", "media/aiapp/picks")
SAVE_SNAPSHOT  = bool(getattr(settings, "AIAPP_SAVE_SNAPSHOT", False))

# リスク・資金まわり（本番では settings.py で上書きOK）
RISK_PCT            = float(getattr(settings, "AIAPP_RISK_PCT", 0.02))               # 許容リスク=総資産×2%
TOTAL_EQUITY        = float(getattr(settings, "AIAPP_TOTAL_EQUITY", 1_000_000))      # 総資産（円）
CASH_BUYING_POWER   = getattr(settings, "AIAPP_CASH_BUYING_POWER", None)             # 現物買付可能額（任意）
MARGIN_BUYING_POWER = getattr(settings, "AIAPP_MARGIN_BUYING_POWER", None)           # 信用余力（任意）
LOT_SIZE            = int(getattr(settings, "AIAPP_LOT_SIZE", 100))                  # ← 単元を100株に固定

TOPN = 10  # 表示件数


# ====== 小ユーティリティ ======

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
    """特徴量からATR系の最新値を安全に取得。なければ0."""
    try:
        atr_cols = [c for c in feat.columns if "ATR" in c.upper()]
        if atr_cols:
            return float(feat[atr_cols[-1]].iloc[-1])
    except Exception:
        pass
    return 0.0

def _score_to_100(score: float) -> int:
    """スコア(-5..+5想定)を0..100へ正規化。"""
    v = (score + 5.0) / 10.0  # 0..1
    return int(max(0, min(100, round(v * 100))))

def _fmt_reason_lines(reasons: Dict[str, float], last_close: float, atr: float) -> List[str]:
    """数値理由を短文＋数字で整形。最後に必要なら懸念を付与。"""
    lines: List[str] = []

    rsi   = reasons.get("rsi")
    macd  = reasons.get("macd_hist")
    vgap  = reasons.get("vwap_gap_pct")
    ret5  = reasons.get("ret5")
    slope = reasons.get("slope")

    if rsi is not None:
        if rsi >= 70:
            lines.append(f"RSI {rsi:.0f}（強め・過熱気味）")
        elif rsi >= 50:
            lines.append(f"RSI {rsi:.0f}（50超え＝上向き）")
        else:
            lines.append(f"RSI {rsi:.0f}（弱め）")

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
    if rsi is not None and rsi > 75:
        concerns.append("RSI過熱")
    if vgap is not None and vgap > 1.5:
        concerns.append("短期乖離が大きい")
    if last_close and atr and (atr / last_close) > 0.04:
        concerns.append("値動きが荒い")
    if concerns:
        lines.append("懸念：" + "・".join(concerns))

    return lines

def _position_sizing(entry: float, sl: float, last_close: float) -> Tuple[int, float, float]:
    """
    リスク基準で株数を決定し、単元（LOT_SIZE）に丸める。
    - 許容リスク金額 = 総資産 × RISK_PCT
    - 1株リスク = max(entry - sl, 最低ティック相当)
    - 生の株数 = floor(許容リスク金額 / 1株リスク)
    - 単元に切り下げ：qty = (生株数 // LOT_SIZE) * LOT_SIZE
    - 現物/信用の買付上限があれば、さらにクリップ
    """
    if entry <= 0 or sl <= 0 or entry <= sl:
        return (0, 0.0, 0.0)

    per_share_risk = max(entry - sl, max(1.0, last_close * 0.001))  # 1円 or 0.1%
    allow_risk = max(TOTAL_EQUITY * RISK_PCT, 1.0)

    raw_qty = int(math.floor(allow_risk / per_share_risk))
    qty = (raw_qty // LOT_SIZE) * LOT_SIZE  # ← ここで100株単元に揃える

    # 現物/信用の上限
    if qty > 0:
        if CASH_BUYING_POWER:
            max_cash_qty = int(CASH_BUYING_POWER // entry)
            max_cash_qty = (max_cash_qty // LOT_SIZE) * LOT_SIZE
            qty = min(qty, max_cash_qty)
        if MARGIN_BUYING_POWER:
            max_margin_qty = int(MARGIN_BUYING_POWER // entry)
            max_margin_qty = (max_margin_qty // LOT_SIZE) * LOT_SIZE
            qty = min(qty, max_margin_qty)

    required_cash = qty * entry if qty > 0 else 0.0
    est_loss = qty * (entry - sl) if qty > 0 else 0.0
    return qty, required_cash, est_loss

def _entry_tp_sl(last_close: float, atr: float, horizon: str, mode: str) -> Tuple[float, float, float]:
    """
    Entry/TP/SLの既定。
    - Entry: 追いかけ防止で軽い押し目待ち（短期×攻め=0.25×ATR押し）
    - TP/SL: ATR係数
    """
    if last_close <= 0:
        return (0.0, 0.0, 0.0)

    if horizon == "short":
        if mode == "aggressive":
            tp_k, sl_k, ent_k = 1.5, 1.0, 0.25
        elif mode == "defensive":
            tp_k, sl_k, ent_k = 1.0, 0.8, 0.10
        else:
            tp_k, sl_k, ent_k = 1.2, 0.9, 0.15
    elif horizon == "mid":
        if mode == "aggressive":
            tp_k, sl_k, ent_k = 2.0, 1.2, 0.35
        elif mode == "defensive":
            tp_k, sl_k, ent_k = 1.4, 1.0, 0.15
        else:
            tp_k, sl_k, ent_k = 1.7, 1.1, 0.25
    else:  # long
        if mode == "aggressive":
            tp_k, sl_k, ent_k = 3.0, 1.6, 0.50
        elif mode == "defensive":
            tp_k, sl_k, ent_k = 2.0, 1.2, 0.20
        else:
            tp_k, sl_k, ent_k = 2.5, 1.4, 0.30

    vol = atr / last_close if (last_close > 0 and atr > 0) else 0.01
    entry = last_close * (1 - ent_k * vol)
    tp    = last_close * (1 + tp_k  * vol)
    sl    = last_close * (1 - sl_k  * vol)
    return entry, tp, sl


# ---- 33業種の名称解決（コードが来ても名称に直す） ----
_SECTOR_NAME_FIELDS = [
    "sector_name", "sector33_name", "industry33_name", "industry_name",
    "sector_jp", "industry_jp"
]
_SECTOR_CODE_FIELDS = [
    "sector_code", "sector33_code", "industry33_code", "industry_code", "sector"
]

# 代表的コードの簡易マップ（来る値が「50」「0050」等のとき）
_SECTOR_CODE_TO_NAME = {
    "50": "食料品", "0050": "食料品",
    "5": "水産・農林業", "0005": "水産・農林業",
    "10": "繊維製品", "0010": "繊維製品",
    "15": "パルプ・紙", "0015": "パルプ・紙",
    "17": "医薬品", "0017": "医薬品",
    "20": "化学", "0020": "化学",
    "25": "ゴム製品", "0025": "ゴム製品",
    "30": "ガラス・土石製品", "0030": "ガラス・土石製品",
    "35": "鉄鋼", "0035": "鉄鋼",
    "40": "非鉄金属", "0040": "非鉄金属",
    "45": "金属製品", "0045": "金属製品",
    "55": "繊維・紙パ", "0055": "繊維・紙パ",  # 予備（誤コード吸収）
    "60": "機械", "0060": "機械",
    "65": "電気機器", "0065": "電気機器",
    "70": "輸送用機器", "0070": "輸送用機器",
    "75": "精密機器", "0075": "精密機器",
    "80": "その他製品", "0080": "その他製品",
    "105": "水産・農林業", "0105": "水産・農林業",  # ベンダ差吸収
}

def _resolve_sector(obj: StockMaster) -> str:
    # まず名称フィールドを試す
    for f in _SECTOR_NAME_FIELDS:
        v = getattr(obj, f, None)
        if v:
            return _nfkc(str(v))
    # 次にコードっぽい値から推定
    for f in _SECTOR_CODE_FIELDS:
        v = getattr(obj, f, None)
        if v is None:
            continue
        s = str(v).strip()
        if s.isdigit() or (len(s) in (2,4) and s.replace("0","").isdigit()):
            name = _SECTOR_CODE_TO_NAME.get(s)
            if not name and len(s) == 2:
                name = _SECTOR_CODE_TO_NAME.get(s.zfill(4))
            if name:
                return name
    # 最後に industry/sector の文字値そのもの（数字なら表示しない）
    fallbacks = [getattr(obj, "sector", None), getattr(obj, "industry33", None), getattr(obj, "industry", None)]
    for v in fallbacks:
        if not v:
            continue
        sv = str(v).strip()
        if not sv.isdigit():
            return _nfkc(sv)
    return "—"


# ====== オンデマンド構築 ======

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
            "score_100": _score_to_100(detail.score),  # 0..100
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
      ?mode=live|demo （既定 live）
      ?reset=1        （セッション初期化）
      ?horizon=short|mid|long
      ?style=aggressive|normal|defensive
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

    # データ取得（DEMO明示時だけデモ読む。LIVEはオンデマンド）
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