# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import os
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

# ===== 既定（ユーザー指定がなければ短期×攻め×淡々） =====
DEFAULT_HORIZON = "short"        # short / mid / long
DEFAULT_MODE    = "aggressive"   # aggressive / normal / defensive
DEFAULT_TONE    = "calm"         # calm(淡々) / neutral / positive

# ユニバース最大件数（重すぎ防止）
UNIVERSE_LIMIT = int(getattr(settings, "AIAPP_UNIVERSE_LIMIT", 400))

# スナップショット（任意保存）。無ければオンデマンドで計算して表示
SNAPSHOT_DIR = getattr(settings, "AIAPP_SNAPSHOT_DIR", "media/aiapp/picks")
SAVE_SNAPSHOT = bool(getattr(settings, "AIAPP_SAVE_SNAPSHOT", False))

# リスク・資金まわり（既定）。本番では settings.py で上書きOK
RISK_PCT = float(getattr(settings, "AIAPP_RISK_PCT", 0.02))             # 許容リスク=総資産×2%
TOTAL_EQUITY = float(getattr(settings, "AIAPP_TOTAL_EQUITY", 1_000_000))  # 総資産の既定（円）
CASH_BUYING_POWER = getattr(settings, "AIAPP_CASH_BUYING_POWER", None)    # 現物買付可能額（任意）
MARGIN_BUYING_POWER = getattr(settings, "AIAPP_MARGIN_BUYING_POWER", None)# 信用余力（任意）

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

def _stars_to_100(score: float) -> int:
    """スコア(-5..+5想定)を0..100へ正規化（見やすい“総合得点”）。"""
    v = (score + 5.0) / 10.0  # 0..1
    return int(max(0, min(100, round(v * 100))))

def _fmt_reason_lines(reasons: Dict[str, float], last_close: float, atr: float) -> List[str]:
    """
    数値理由をテキスト化 + 懸念があれば最後に追加。
    ・親しみやすい短文＋数字
    """
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

    # ボラの説明
    if last_close > 0 and atr > 0:
        vol_pct = atr / last_close * 100
        lines.append(f"ボラ目安 ATR={atr:.1f}円（株価比 {vol_pct:.1f}%）")

    # 懸念自動付与
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
    リスク基準で株数を決定。
    - 許容リスク金額 = 総資産 × RISK_PCT
    - 1株リスク = max(entry - sl, 最低1ティック相当)
    - 株数 = floor(許容リスク金額 / 1株リスク)
    - 買付余力（現物/信用）が与えられていれば、その上限でクリップ
    """
    if entry <= 0 or sl <= 0 or entry <= sl:
        return (0, 0.0, 0.0)

    per_share_risk = max(entry - sl, max(1.0, last_close * 0.001))  # 1円 or 0.1%
    allow_risk = max(TOTAL_EQUITY * RISK_PCT, 1.0)
    qty = int(math.floor(allow_risk / per_share_risk))

    if qty > 0:
        if CASH_BUYING_POWER:
            max_cash_qty = int(CASH_BUYING_POWER // entry)
            qty = min(qty, max_cash_qty)
        if MARGIN_BUYING_POWER:
            max_margin_qty = int(MARGIN_BUYING_POWER // entry)
            qty = min(qty, max_margin_qty)

    required_cash = qty * entry if qty > 0 else 0.0
    est_loss = qty * (entry - sl) if qty > 0 else 0.0
    return qty, required_cash, est_loss

def _entry_tp_sl(last_close: float, atr: float, horizon: str, mode: str) -> Tuple[float, float, float]:
    """
    Entry/TP/SLの既定。
    - Entry: “追いかけすぎ防止”で軽い押し目待ち（短期×攻めは 0.25×ATR 押し）
    - TP/SL: ATR係数で決定
    """
    if last_close <= 0:
        return (0.0, 0.0, 0.0)

    # 係数
    if horizon == "short":
        if mode == "aggressive":
            tp_k, sl_k, ent_k = 1.5, 1.0, 0.25  # ← ここで Entry を下げる
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

    vol = atr / last_close if (last_close > 0 and atr > 0) else 0.01  # 最低1%相当
    entry = last_close * (1 - ent_k * vol)   # ← 押し目待ち
    tp    = last_close * (1 + tp_k  * vol)
    sl    = last_close * (1 - sl_k  * vol)
    return entry, tp, sl


# ====== オンデマンド構築 ======

def _build_live_items(horizon: str, mode: str, topn: int = TOPN) -> List[Dict]:
    # ここは values() で列名がズレるのを避けるため、オブジェクトで取得して getattr で安全に拾う
    qs = StockMaster.objects.all().order_by("code")[:UNIVERSE_LIMIT]
    got: List[Dict] = []

    for obj in qs:
        code = getattr(obj, "code", "")
        name = getattr(obj, "name", "")
        sector = (
            getattr(obj, "sector", None)
            or getattr(obj, "sector33", None)
            or getattr(obj, "industry33", None)
            or getattr(obj, "industry", None)
            or "-"
        )

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

        reasons_text = _fmt_reason_lines(detail.reasons, last_close, atr)

        got.append({
            "code": code,
            "name": name,
            "name_norm": _nfkc(name),
            "sector": _nfkc(str(sector)),

            "score": round(detail.score, 3),
            "score_100": _stars_to_100(detail.score),   # 0..100 の総合得点
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

    # データ取得（DEMO明示時だけデモ読む。LIVEはオンデマンドで出す）
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
    }
    return render(request, "aiapp/picks.html", context)