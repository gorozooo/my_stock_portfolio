# -*- coding: utf-8 -*-
"""
picks_build v4.7 (robust: chunked + wait + incremental snapshots)

- --universe all | nk225
- タイムバジェット内で段階スナップショットを吐く
- チャンク毎に wait(timeout) → done だけ集計、not_done は cancel して前進
"""

from __future__ import annotations
import json, os, sys, time, random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from django.conf import settings
from django.core.management.base import BaseCommand

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample

# -------------------- constants / tunables --------------------
JST = timezone(timedelta(hours=9))
def _now_jst(): return datetime.now(JST)
def _log(msg): print(f"[picks_build] {msg}", flush=True)

MAX_WORKERS      = int(os.environ.get("AIAPP_BUILD_WORKERS", "12"))
FAST_BARS        = int(os.environ.get("AIAPP_FAST_BARS", "30"))
FAST_BARS_RESCUE = int(os.environ.get("AIAPP_FAST_BARS2", "60"))
DEEP_BARS        = int(os.environ.get("AIAPP_DEEP_BARS", "180"))
MIN_BARS         = int(os.environ.get("AIAPP_MIN_BARS", "60"))

FAST_TIMEOUT   = float(os.environ.get("AIAPP_FAST_TIMEOUT", "3.5"))   # sec / item
DEEP_TIMEOUT   = float(os.environ.get("AIAPP_DEEP_TIMEOUT", "7.5"))   # sec / item
CHUNK_SIZE     = int(os.environ.get("AIAPP_CHUNK_SIZE", "40"))        # items per batch
DEFAULT_BUDGET = int(os.environ.get("AIAPP_BUDGET_SEC", "360"))       # hard wall
SNAPSHOT_EVERY = int(os.environ.get("AIAPP_SNAPSHOT_SEC", "30"))      # write interim

PREFINAL_TOPK = int(os.environ.get("AIAPP_PREFINAL", "120"))
TARGET_TOPN   = 10

LOT_SIZE         = 100
TARGET_LOSS_JPY  = 20000.0
DEFAULT_HORIZON  = "short"
DEFAULT_MODE     = "aggressive"
DEFAULT_TONE     = "friendly"

MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR  = MEDIA_ROOT / "aiapp" / "picks"
PICKS_DIR.mkdir(parents=True, exist_ok=True)
LOCK_PATH  = PICKS_DIR / ".picks_build.lock"

# -------------------- NK225 optional --------------------
NK225_CODES: List[str] = []
try:
    from aiapp.universe.nikkei225 import CODES as NK225_CODES  # noqa: F401
except Exception:
    NK225_CODES = []

# -------------------- lock --------------------
class BuildLock:
    def __init__(self, path: Path, force=False):
        self.path, self.force, self.acq = path, force, False
    def __enter__(self):
        if self.path.exists() and not self.force:
            _log("another build is running; exit 202"); sys.exit(202)
        self.path.write_text(str(os.getpid())); self.acq = True; return self
    def __exit__(self, *_):
        if self.acq: self.path.unlink(missing_ok=True)

# -------------------- universe --------------------
BLOCK_KEYWORDS = ["ＥＴＦ","ETF","ＲＥＩＴ","REIT","投資法人","連動型","指数","インデックス"]

@dataclass
class URow:
    code: str; name: str; sector_name: Optional[str] = None

def _looks_index_like(code, name) -> bool:
    if any(k in name for k in BLOCK_KEYWORDS): return True
    try: c = int(code)
    except: return True
    return 1300 <= c <= 1399

def _universe_all() -> List[URow]:
    rows = []
    for c,n,s in StockMaster.objects.all().values_list("code","name","sector_name"):
        c = str(c); n = str(n or "")
        if _looks_index_like(c, n): continue
        rows.append(URow(c, n, s or None))
    random.seed(42); random.shuffle(rows)
    return rows

def _universe_nk225() -> List[URow]:
    codes = set(str(x) for x in NK225_CODES)
    if not codes:
        _log("WARN: NK225 list empty; fallback to ALL.")
        return _universe_all()
    rows = [URow(str(c), str(n or ""), s or None)
            for c,n,s in StockMaster.objects.filter(code__in=codes)
            .values_list("code","name","sector_name")]
    random.seed(42); random.shuffle(rows)
    return rows

# -------------------- scoring helpers --------------------
def _calc_fast_score(code: str) -> Optional[Tuple[str, float, float]]:
    def _try(nbars: int):
        df = get_prices(code, nbars)
        if df is None or len(df) < 20: return None
        close = float(df["close"].iloc[-1])
        r5  = (close/float(df["close"].iloc[-5]) - 1.0) if len(df) > 5 else 0.0
        r20 = (close/float(df["close"].iloc[-20]) - 1.0) if len(df) > 20 else 0.0
        hi = df.get("high", df["close"]); lo = df.get("low", df["close"])
        atr = float((hi - lo).abs().tail(14).mean()); atr_pct = atr / max(1e-6, close)
        fast = max(0, min(100, 50 + (r5*100*0.4 + r20*100*0.6 - atr_pct*100*0.5)))
        return (code, close, round(fast, 2))
    try:
        return _try(FAST_BARS) or _try(FAST_BARS_RESCUE)
    except Exception:
        return None

def _confidence_from_feat(feat) -> float:
    try:
        last = feat.iloc[-1]
        atr = float(last.get("atr_pct", 0.02))
        nanr = float((feat.isna().sum(axis=1).iloc[-60:] > 0).mean())
        base = 3.0 + (0.5 if atr < 0.03 else 0) + (-0.5 if nanr > 0.1 else 0.5)
        return float(max(1, min(5, round(base, 2))))
    except Exception:
        return 2.5

def _build_deep(row: URow) -> Optional[Dict[str, Any]]:
    try:
        df = get_prices(row.code, DEEP_BARS)
        if df is None or len(df) < MIN_BARS: return None
        feat = compute_features(df)
        if feat is None or feat.empty: return None
        score = float(score_sample(feat, mode=DEFAULT_MODE, horizon=DEFAULT_HORIZON))
        score = max(0, min(100, score))
        conf = _confidence_from_feat(feat)
        close = float(df["close"].iloc[-1])
        entry, tp, sl = round(close*0.994, 1), round(close*1.045, 1), round(close*0.965, 1)
        pl = max(1.0, entry - sl)
        qty = max(LOT_SIZE, int(TARGET_LOSS_JPY / pl / LOT_SIZE) * LOT_SIZE)
        return dict(
            code=row.code, name=row.name, sector=row.sector_name or "", price=close,
            score=round(score,1), confidence=conf, entry=entry, tp=tp, sl=sl, qty=qty,
            required_cash=int(entry*qty),
            exp_profit=int((tp-entry)*qty), exp_loss=int((entry-sl)*qty),
            reasons=["RSI/ROC/VWAP/ATR/相対強度の合成（短期×攻め）"]
        )
    except Exception:
        return None

# -------------------- snapshots --------------------
def _save_json(items: List[Dict[str,Any]], tag: str):
    meta = {
        "ts": _now_jst().isoformat(timespec="seconds"),
        "mode": DEFAULT_MODE, "horizon": DEFAULT_HORIZON, "tone": DEFAULT_TONE,
        "version": tag,
    }
    text = json.dumps({"meta": meta, "items": items}, ensure_ascii=False, indent=2)
    latest = PICKS_DIR / f"latest_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
    hist   = PICKS_DIR / f"{_now_jst():%Y%m%d_%H%M%S}_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
    latest.write_text(text, encoding="utf-8")
    hist.write_text(text, encoding="utf-8")
    return latest

def _emit_fallback_from_fast(fast_map: Dict[str, Tuple[float,float]], uni_index: Dict[str, URow], label: str):
    pre = sorted(fast_map.items(), key=lambda kv: kv[1][1], reverse=True)[:TARGET_TOPN]
    fb = []
    for code, (close, fastv) in pre:
        row = uni_index.get(code, URow(code, code, ""))
        entry, tp, sl = round(close*0.996, 1), round(close*1.03, 1), round(close*0.975, 1)
        pl = max(1.0, entry - sl)
        qty = max(LOT_SIZE, int(TARGET_LOSS_JPY / pl / LOT_SIZE) * LOT_SIZE)
        fb.append(dict(
            code=row.code, name=row.name, sector=row.sector_name or "", price=close,
            score=round(fastv,1), confidence=2.5, entry=entry, tp=tp, sl=sl, qty=qty,
            required_cash=int(entry*qty),
            exp_profit=int((tp-entry)*qty), exp_loss=int((entry-sl)*qty),
            reasons=["暫定：予選スコアからのフォールバック"]
        ))
    _save_json(fb, f"{label}-fallback")
    return fb

# -------------------- command --------------------
class Command(BaseCommand):
    help = "Build AI picks snapshot (time-budgeted & chunked & incremental)"

    def add_arguments(self, p):
        p.add_argument("--sample", type=int, default=None)
        p.add_argument("--force", action="store_true")
        p.add_argument("--universe", type=str, default="all", choices=["all","nk225"])
        p.add_argument("--budget", type=int, default=DEFAULT_BUDGET)

    def handle(self, *_, **o):
        start = time.time()
        force    = bool(o.get("force", False))
        sample   = o.get("sample")
        universe = o.get("universe", "all")
        budget   = int(o.get("budget") or DEFAULT_BUDGET)

        _log(f"start universe={universe} sample={sample} budget={budget}s")
        with BuildLock(LOCK_PATH, force=force):
            items = self._build(start, universe, sample, budget)
        _log(f"done items={len(items)} dur={round(time.time()-start,1)}s")

    # --------------- main ---------------
    def _build(self, t0: float, universe: str, sample: Optional[int], budget: int):
        # universe
        uni = _universe_nk225() if universe == "nk225" else _universe_all()
        if sample: uni = uni[:int(sample)]
        _log(f"universe={len(uni)}")
        uni_index = {r.code: r for r in uni}

        def timeup() -> bool:
            return (time.time() - t0) > budget

        # fast stage
        fast: Dict[str, Tuple[float,float]] = {}
        last_snap = 0.0

        for i in range(0, len(uni), CHUNK_SIZE):
            if timeup():
                _log("timeout during fast stage")
                return _emit_fallback_from_fast(fast, uni_index, "fast-timeout") if fast else self._emit_empty()

            chunk = uni[i:i+CHUNK_SIZE]
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = [ex.submit(_calc_fast_score, r.code) for r in chunk]
                # チャンクの総タイムアウト（ゆるめ）
                chunk_timeout = max(1.0, FAST_TIMEOUT * len(chunk))
                done, not_done = wait(futures, timeout=chunk_timeout, return_when=FIRST_COMPLETED)
                # 完了分はさらに残りも一応待って回収。ただし個別は短いtimeoutで。
                end_time = time.time() + chunk_timeout
                # すでに完了分
                for fu in list(done):
                    try:
                        r = fu.result(timeout=0.01)
                        if r: fast[r[0]] = (r[1], r[2])
                    except Exception:
                        pass
                # 残りを短いタイムアウトで回収
                for fu in not_done:
                    remain = max(0.0, end_time - time.time())
                    try:
                        r = fu.result(timeout=min(FAST_TIMEOUT, remain))
                        if r: fast[r[0]] = (r[1], r[2])
                    except Exception:
                        # 間に合わない・例外は捨てて次へ
                        fu.cancel()

            # 途中スナップショット
            if (time.time() - last_snap) >= SNAPSHOT_EVERY and fast:
                pre = sorted(fast.items(), key=lambda kv: kv[1][1], reverse=True)[:TARGET_TOPN]
                temp = []
                for code, (close, fastv) in pre:
                    row = uni_index.get(code, URow(code, code, ""))
                    entry, tp, sl = round(close*0.996, 1), round(close*1.03, 1), round(close*0.975, 1)
                    pl = max(1.0, entry - sl)
                    qty = max(LOT_SIZE, int(TARGET_LOSS_JPY / pl / LOT_SIZE) * LOT_SIZE)
                    temp.append(dict(
                        code=row.code, name=row.name, sector=row.sector_name or "", price=close,
                        score=round(fastv,1), confidence=2.3, entry=entry, tp=tp, sl=sl, qty=qty,
                        required_cash=int(entry*qty),
                        exp_profit=int((tp-entry)*qty), exp_loss=int((entry-sl)*qty),
                        reasons=["暫定：予選中の途中経過"],
                    ))
                _save_json(temp, "fast-interim")
                last_snap = time.time()

        _log(f"fast_pass={len(fast)}")
        if not fast:
            _log("no fast pass; emit empty")
            return self._emit_empty()

        if (time.time() - t0) > budget:
            _log("timeout right after fast stage")
            return _emit_fallback_from_fast(fast, uni_index, "post-fast")

        # finalists
        pre = sorted(fast.items(), key=lambda kv: kv[1][1], reverse=True)
        finalists = {c for c,_ in pre[:max(60, min(PREFINAL_TOPK, len(pre)))]}
        _log(f"finalists={len(finalists)}")

        # deep stage
        results: List[Dict[str,Any]] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(_build_deep, uni_index[c]): c for c in finalists if c in uni_index}
            end_time = time.time() + max(1.0, DEEP_TIMEOUT * max(1, len(futs)))
            for fu in list(futs.keys()):
                remain = max(0.0, end_time - time.time())
                if timeup() or remain <= 0: break
                try:
                    r = fu.result(timeout=min(DEEP_TIMEOUT, remain))
                    if r: results.append(r)
                except Exception:
                    pass

        results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        top = results[:TARGET_TOPN]
        if not top:
            _log("deep stage produced 0; fallback from fast")
            return _emit_fallback_from_fast(fast, uni_index, "deep-empty")

        _save_json(top, "final")
        return top

    def _emit_empty(self):
        _save_json([], "empty")
        return []