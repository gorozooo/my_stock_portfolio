# -*- coding: utf-8 -*-
from __future__ import annotations

import json, math, os, time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices

# フル評価（入っていれば使う／無くてもOK）
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

# ---- パラメータ（安全寄り） ---------------------------------------------------
TARGET_TOPN          = 10

FAST_MIN_BARS        = 80    # フル評価に必要な最低本数
LITE_MIN_BARS        = 20    # 軽量は20本でOK
LITE_NBARS_DEFAULT   = 60    # 軽量が読むバー数

MAX_WORKERS          = max(4, os.cpu_count() or 8)  # 並列数
PER_TASK_TIMEOUT     = 6.0   # 1銘柄あたりの許容秒（Lite）
SYNTHETIC_TRIES_MAX  = 80    # 合成フォールバックで試す銘柄数上限

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
        # symlink不可環境向け
        latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[picks_build] wrote {path.name} items={len(items)}")
    return path

def _load_universe(label: Optional[str], sample: Optional[int]) -> List[Tuple[str,str,str]]:
    qs = StockMaster.objects.all().values_list("code","name","sector_name").order_by("code")

    if label == "nk225":
        # ファイルがあれば厳密抽出、なければ 250件程度の近似でも OK
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
    return [(c, n, s or "") for (c, n, s) in rows]

# ---- 軽量スコア（並列・個別タイムアウト・早期打ち切り） -----------------------
def _rank_light_single(code: str, nbars: int) -> Optional[Tuple[float,float,float,float,float,List[str]]]:
    df = get_prices(code, nbars)
    if df is None or df.empty:
        return None
    df = df.dropna(subset=["close"])
    if len(df) < LITE_MIN_BARS:
        return None

    close = df["close"].astype("float")
    last  = float(close.iloc[-1])

    def pct(n: int) -> float:
        if len(close) <= n or float(close.iloc[-n-1]) == 0:
            return 0.0
        return (float(close.iloc[-1]) / float(close.iloc[-n-1]) - 1.0) * 100.0

    roc5, roc20 = pct(5), pct(20)

    if {"high","low"} <= set(df.columns):
        atr = float((df["high"] - df["low"]).abs().tail(14).mean())
    else:
        atr = float(close.pct_change().abs().tail(14).mean() * last)
    atr_pct = 0.0 if last == 0 else (atr / last) * 100.0

    # 標準化（サンプル内 z）
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
        "（軽量）",
    ]
    return (float(score), float(ai_conf), last, tp, sl, reasons)

def _collect_light_parallel(rows: List[Tuple[str,str,str]], nbars: int, budget: int) -> List[PickItem]:
    """
    並列で Lite 評価。個々のタスクは PER_TASK_TIMEOUT 秒で見切る。
    TopN が集まったら即終了。
    """
    start = time.time()
    picks: List[PickItem] = []

    def task(row):
        code, name, sector = row
        t0 = time.time()
        try:
            r = _rank_light_single(code, nbars)
        except Exception:
            r = None
        # 個別タイムアウト（実時間で判定）
        if time.time() - t0 > PER_TASK_TIMEOUT:
            return None
        if r is None:
            return None
        sc, conf, last, tp, sl, reasons = r
        return PickItem(code, name, sector, sc, conf, last, last, tp, sl, reasons)

    # 先頭から順に投げる（キャッシュが効きやすい）
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(task, row): row for row in rows}
        for fut in as_completed(futures):
            if time.time() - start > budget * 0.95:
                # 予算使い切り近くで打ち切り
                break
            item = fut.result()
            if item:
                picks.append(item)
                if len(picks) >= TARGET_TOPN:
                    break

    return picks

# ---- フル評価（任意・重い） ---------------------------------------------------
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
    help = "Build AI picks snapshot (short x aggressive). 並列Liteで必ずTopNを埋める。"

    def add_arguments(self, parser):
        parser.add_argument("--universe", type=str, default=None, help="nk225 / all / None")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--budget", type=int, default=180)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--lite-only", action="store_true")
        parser.add_argument("--nbars-lite", type=int, default=LITE_NBARS_DEFAULT)

    def handle(self, *args, **opts):
        label     = opts.get("universe")
        sample    = opts.get("sample")
        budget    = int(opts.get("budget") or 180)
        lite_only = bool(opts.get("lite-only"))
        nbars_lite = int(opts.get("nbars-lite") or LITE_NBARS_DEFAULT)

        start = time.time()
        rows  = _load_universe(label, sample)
        print(f"[picks_build] start universe={label or 'all'} sample={sample} budget={budget}s")
        print(f"[picks_build] universe={len(rows)}")

        # 1) フル（任意・時間に余裕があれば）
        if not lite_only and compute_features is not None and score_sample is not None:
            fast: List[PickItem] = []
            for code, name, sector in rows:
                if time.time() - start > budget * 0.5:
                    print("[picks_build] switch to lite (budget guard)")
                    break
                try:
                    r = _rank_full(code)
                except Exception:
                    r = None
                if not r:
                    continue
                sc, conf, last, tp, sl, reasons = r
                fast.append(PickItem(code, name, sector, sc, conf, last, last, tp, sl, reasons))
                if len(fast) >= TARGET_TOPN * 3:
                    break
            if fast:
                fast.sort(key=lambda x: x.score, reverse=True)
                top = fast[:TARGET_TOPN]
                _emit_snapshot(top, "full")
                print(f"[picks_build] done items={len(top)} dur={time.time()-start:.1f}s")
                return
            else:
                print("[picks_build] fast_pass=0")

        # 2) 並列Lite（個別タイムアウト・TopNで即終了）
        lite = _collect_light_parallel(rows, nbars_lite, budget)
        if lite:
            lite.sort(key=lambda x: x.score, reverse=True)
            top = lite[:TARGET_TOPN]
            _emit_snapshot(top, "lite")
            print(f"[picks_build] done items={len(top)} dur={time.time()-start:.1f}s")
            return

        # 3) 最後のフォールバック（価格だけで暫定10件）
        print("[picks_build] lite=0; emit synthetic fallback")
        synth: List[PickItem] = []
        tried = 0
        for code, name, sector in rows:
            tried += 1
            if tried > SYNTHETIC_TRIES_MAX:
                break
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