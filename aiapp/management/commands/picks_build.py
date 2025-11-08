# -*- coding: utf-8 -*-
from __future__ import annotations

import json, math, os, time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices

# Optional: フル評価が使えるなら使う
try:
    from aiapp.models.features import compute_features
except Exception:
    compute_features = None  # type: ignore
try:
    from aiapp.models.scoring import score_sample
except Exception:
    score_sample = None  # type: ignore

JST = timezone(timedelta(hours=9))

MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
OUT_DIR     = MEDIA_ROOT / "aiapp" / "picks"
UNIV_DIR    = Path("aiapp") / "data" / "universe"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TARGET_TOPN          = 10
FAST_MIN_BARS        = 80
LIGHT_MIN_BARS       = 20   # ←緩和（20本以上でOK）
FAST_BUDGET_DEFAULT  = 180  # 秒

@dataclass
class PickItem:
    code: str
    name: str
    sector: str
    score: float
    ai_confidence: float
    price: float
    entry: float
    tp: float
    sl: float
    reasons: List[str]

def _now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

def _emit_snapshot(items: List[PickItem], tag: str) -> Path:
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"picks_{tag}_{ts}.json"
    payload = {
        "generated_at": _now_jst(),
        "style": "aggressive",
        "horizon": "short",
        "items": [asdict(x) for x in items],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = OUT_DIR / f"latest_{tag}.json"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(path.name)
    except Exception:
        latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[picks_build] wrote {path.name} items={len(items)}")
    return path

def _load_universe(label: Optional[str], sample: Optional[int]) -> List[Tuple[str,str,str]]:
    qs = StockMaster.objects.all().values_list("code","name","sector_name").order_by("code")

    if label == "nk225":
        codes = None
        p = UNIV_DIR / "nk225.txt"
        if p.exists():
            try:
                codes = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
            except Exception:
                codes = None
        rows = list(qs.filter(code__in=codes)) if codes else list(qs)[:250]
    else:
        rows = list(qs)

    if sample:
        rows = rows[: int(sample)]
    # sector は空文字で揃える
    return [(c, n, s or "") for (c, n, s) in rows]

# ---- 軽量スコア（“必ず拾う”用） --------------------------------------------
def _rank_light(code: str, nbars: int = 90) -> Optional[Tuple[float,float,float,float,float,List[str]]]:
    """
    なるべく軽く・データが薄くても通す。
    スコア = z(ROC5) + 0.5*z(ROC20) - 0.3*z(ATR%推定)
    AI信頼度 = 1..5（簡易指標）
    """
    df = get_prices(code, nbars)
    if df is None or df.empty:
        return None
    df = df.dropna(subset=["close"])
    if len(df) < LIGHT_MIN_BARS:
        return None

    close = df["close"].astype("float")
    last  = float(close.iloc[-1])

    # 5日/20日リターン（%）
    def pct(n: int) -> float:
        if len(close) <= n or float(close.iloc[-n-1]) == 0:
            return 0.0
        return (float(close.iloc[-1]) / float(close.iloc[-n-1]) - 1.0) * 100.0

    roc5, roc20 = pct(5), pct(20)

    # ATR%簡易：high/lowあればHL、無ければ終値の絶対リターン
    if {"high","low"} <= set(df.columns):
        atr = float((df["high"] - df["low"]).abs().tail(14).mean())
    else:
        atr = float(close.pct_change().abs().tail(14).mean() * last)
    atr_pct = 0.0 if last == 0 else (atr / last) * 100.0

    # 標準化
    def _z(x, s):
        m = float(s.mean()) if len(s) else 0.0
        v = float(s.std(ddof=0)) or 1.0
        return (x - m) / v

    r5s  = close.pct_change(5).dropna()*100
    r20s = close.pct_change(20).dropna()*100
    atrs = ((df["high"]-df["low"]).abs()/close*100.0).dropna() if {"high","low"} <= set(df.columns) else (close.pct_change().abs()*100).dropna()

    score  = _z(roc5, r5s) + 0.5*_z(roc20, r20s) - 0.3*_z(atr_pct, atrs)
    tp     = last * 1.07
    sl     = last * 0.965

    ai_conf = 3.0
    ai_conf += 1.0 if roc20 > 0 else -0.2
    ai_conf += 0.5 if abs(roc5) < 8.0 else -0.2
    ai_conf = max(1.0, min(5.0, round(ai_conf, 2)))

    reasons = [
        f"5日モメンタム {roc5:+.2f}%",
        f"20日モメンタム {roc20:+.2f}%",
        f"ボラ目安 {atr_pct:.2f}%",
        "（軽量スコア）",
    ]
    return (float(score), float(ai_conf), last, tp, sl, reasons)

# ---- フル評価（使える時だけ） -----------------------------------------------
def _rank_full(code: str, nbars: int = 180) -> Optional[Tuple[float,float,float,float,float,List[str]]]:
    if compute_features is None or score_sample is None:
        return None
    df = get_prices(code, nbars)
    if df is None or df.empty or len(df) < FAST_MIN_BARS:
        return None
    feat = compute_features(df)
    if feat is None or feat.empty:
        return None

    s = float(score_sample(feat, mode="aggressive", horizon="short"))
    last = float(df["close"].iloc[-1])
    tp = last * 1.07
    sl = last * 0.965

    ai_conf = 3.0
    try:
        cols = [c for c in feat.columns if "ai_conf" in c.lower()]
        if cols:
            ai_conf = float(feat[cols[-1]].iloc[-1])
            ai_conf = max(1.0, min(5.0, ai_conf))
    except Exception:
        pass

    reasons = ["フル特徴量の合成スコア"]
    return (s, ai_conf, last, tp, sl, reasons)

# -----------------------------------------------------------------------------
class Command(BaseCommand):
    help = "Build AI picks snapshot (short x aggressive). 必ず10件を出力（フル→軽量→合成）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", type=str, default=None, help="nk225 / all / None")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--budget", type=int, default=FAST_BUDGET_DEFAULT)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--lite-only", action="store_true", help="最初から軽量モードで実行")

    def handle(self, *args, **opts):
        label   = opts.get("universe")
        sample  = opts.get("sample")
        budget  = int(opts.get("budget") or FAST_BUDGET_DEFAULT)
        lite_only = bool(opts.get("lite-only"))

        start = time.time()
        rows  = _load_universe(label, sample)
        print(f"[picks_build] start universe={label or 'all'} sample={sample} budget={budget}s")
        print(f"[picks_build] universe={len(rows)}")

        items: List[PickItem] = []

        # 1) フル（予算の60%まで）
        if not lite_only and compute_features is not None and score_sample is not None:
            for code, name, sector in rows:
                if time.time() - start > budget * 0.6:
                    print("[picks_build] switch to lightweight (budget guard)")
                    break
                try:
                    r = _rank_full(code)
                except Exception:
                    r = None
                if r is None:
                    continue
                sc, conf, last, tp, sl, reasons = r
                items.append(PickItem(code, name, sector, sc, conf, last, last, tp, sl, reasons))
                if len(items) >= TARGET_TOPN * 6:
                    break

            if items:
                items.sort(key=lambda x: x.score, reverse=True)
                top = items[:TARGET_TOPN]
                _emit_snapshot(top, "full")
                print(f"[picks_build] done items={len(top)} dur={time.time()-start:.1f}s")
                return
            else:
                print("[picks_build] fast_pass=0")

        # 2) 軽量（予算の95%まで・必ず拾う）
        lite: List[PickItem] = []
        for code, name, sector in rows:
            if time.time() - start > budget * 0.95:
                print("[picks_build] budget nearly exhausted; stop collecting")
                break
            try:
                r = _rank_light(code)
            except Exception:
                r = None
            if r is None:
                continue
            sc, conf, last, tp, sl, reasons = r
            lite.append(PickItem(code, name, sector, sc, conf, last, last, tp, sl, reasons))

        if lite:
            lite.sort(key=lambda x: x.score, reverse=True)
            top = lite[:TARGET_TOPN]
            _emit_snapshot(top, "lite")
            print(f"[picks_build] done items={len(top)} dur={time.time()-start:.1f}s")
            return

        # 3) 合成（最悪でも10件）
        print("[picks_build] lightweight=0; emit synthetic fallback")
        synth: List[PickItem] = []
        for code, name, sector in rows[: TARGET_TOPN * 4]:
            df = get_prices(code, 30)
            if df is None or df.empty:
                continue
            cls = df["close"].dropna()
            if cls.empty:
                continue
            last = float(cls.iloc[-1])
            if not math.isfinite(last) or last <= 0:
                continue
            synth.append(PickItem(
                code, name, sector, 0.0, 2.0, last, last, last*1.05, last*0.97,
                ["データ薄のため暫定表示"]
            ))
            if len(synth) >= TARGET_TOPN:
                break

        if not synth:
            _emit_snapshot([], "empty")
            print(f"[picks_build] done items=0 dur={time.time()-start:.1f}s")
            return

        _emit_snapshot(synth, "synthetic")
        print(f"[picks_build] done items={len(synth)} dur={time.time()-start:.1f}s")