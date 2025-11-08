# -*- coding: utf-8 -*-
"""
picks_build (staged + hard timeout fallback)
300銘柄でも6分で自動停止 → 暫定TOP10を確実に生成。
"""
from __future__ import annotations
import json, os, sys, time, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample

# ========= 設定 =========
MAX_WORKERS = 12
FAST_BARS = 30
FAST_BARS_RESCUE = 60
DEEP_BARS = 180
MIN_BARS = 60
HARD_BUDGET_SEC = 360  # ←6分制限
PREFINAL_TOPK = 120
LOT_SIZE = 100
TARGET_LOSS_JPY = 20000.0

MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR = MEDIA_ROOT / "aiapp" / "picks"
PICKS_DIR.mkdir(parents=True, exist_ok=True)
LOCK_PATH = PICKS_DIR / ".picks_build.lock"

DEFAULT_HORIZON = "short"
DEFAULT_MODE = "aggressive"
DEFAULT_TONE = "friendly"
JST = timezone(timedelta(hours=9))
def _now_jst(): return datetime.now(JST)
def _log(msg): print(f"[picks_build] {msg}", flush=True)

# ========= ロック =========
class BuildLock:
    def __init__(self, path: Path, force=False):
        self.path, self.force, self.acquired = path, force, False
    def __enter__(self):
        if self.path.exists() and not self.force:
            _log("another build is running; exit 202"); sys.exit(202)
        self.path.write_text(str(os.getpid())); self.acquired = True
        return self
    def __exit__(self, *_):
        if self.acquired: self.path.unlink(missing_ok=True)

# ========= ユニバース =========
BLOCK_KEYWORDS = ["ＥＴＦ", "ETF", "ＲＥＩＴ", "REIT", "投資法人", "連動型", "指数", "インデックス"]
@dataclass
class URow:
    code: str; name: str; sector_name: Optional[str]=None
def _looks_index_like(code, name):
    if any(k in name for k in BLOCK_KEYWORDS): return True
    try: c = int(code)
    except: return True
    return 1300 <= c <= 1399
def _iter_universe(limit=None):
    qs = StockMaster.objects.all().values_list("code","name","sector_name")
    rows = [URow(str(c),str(n),s or None) for c,n,s in qs if not _looks_index_like(str(c),str(n))]
    random.seed(42); random.shuffle(rows)
    if limit: rows = rows[:limit]
    _log(f"universe(filtered)={len(rows)}")
    return rows

# ========= 予選 =========
def _calc_fast_score(code:str)->Optional[Tuple[str,float,float]]:
    def _try(n):
        df=get_prices(code,n)
        if df is None or len(df)<20: return None
        close=float(df["close"].iloc[-1])
        r5=(close/float(df["close"].iloc[-5])-1.0) if len(df)>5 else 0
        r20=(close/float(df["close"].iloc[-20])-1.0) if len(df)>20 else 0
        hi=df.get("high",df["close"]); lo=df.get("low",df["close"])
        atr=float((hi-lo).abs().tail(14).mean()); atr_pct=atr/max(1e-6,close)
        fast=max(0,min(100,50+(r5*100*0.4+r20*100*0.6-atr_pct*100*0.5)))
        return (code,close,round(fast,2))
    try:
        out=_try(FAST_BARS) or _try(FAST_BARS_RESCUE)
        return out
    except: return None

# ========= 本選 =========
def _confidence_from_feat(f):
    try:
        last=f.iloc[-1]; atr=float(last.get("atr_pct",0.02))
        nanr=float((f.isna().sum(axis=1).iloc[-60:]>0).mean())
        base=3.0+(0.5 if atr<0.03 else 0)+(-0.5 if nanr>0.1 else 0.5)
        return float(max(1,min(5,round(base,2))))
    except: return 2.5
def _build_deep(r:URow)->Optional[Dict[str,Any]]:
    try:
        df=get_prices(r.code,DEEP_BARS)
        if df is None or len(df)<MIN_BARS: return None
        feat=compute_features(df)
        if feat is None or feat.empty: return None
        score=float(score_sample(feat,mode=DEFAULT_MODE,horizon=DEFAULT_HORIZON))
        score=max(0,min(100,score)); conf=_confidence_from_feat(feat)
        close=float(df["close"].iloc[-1])
        entry, tp, sl = round(close*0.994,1), round(close*1.045,1), round(close*0.965,1)
        pl=max(1.0,entry-sl); qty=max(LOT_SIZE,int(TARGET_LOSS_JPY/pl/LOT_SIZE)*LOT_SIZE)
        return dict(code=r.code,name=r.name,sector=r.sector_name or "",price=close,
            score=round(score,1),confidence=conf,entry=entry,tp=tp,sl=sl,qty=qty,
            required_cash=int(entry*qty),
            exp_profit=int((tp-entry)*qty),exp_loss=int((entry-sl)*qty),
            reasons=["RSIなど特徴量から自動算出"])
    except: return None

# ========= コマンド =========
class Command(BaseCommand):
    help="Build AI picks snapshot (fast with hard timeout)"
    def add_arguments(self,p):
        p.add_argument("--sample",type=int,default=None)
        p.add_argument("--force",action="store_true")
    def handle(self,*_,**o):
        start=time.time(); sample=o.get("sample"); force=o.get("force",False)
        _log(f"start sample={sample}")
        with BuildLock(LOCK_PATH,force=force):
            items=self._build_snapshot(sample,start)
        _log(f"done items={len(items)} dur={round(time.time()-start,1)}s")
    # ---- main
    def _build_snapshot(self,sample,start):
        uni=_iter_universe(limit=sample)
        fast={}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for f in as_completed({ex.submit(_calc_fast_score,r.code):r for r in uni}):
                if time.time()-start>HARD_BUDGET_SEC:
                    _log("timeout during fast stage"); return self._emit_fallback(fast,uni)
                try:
                    r=f.result(timeout=10)
                    if r: fast[r[0]]=(r[1],r[2])
                except: pass
        _log(f"fast_pass={len(fast)}")
        if time.time()-start>HARD_BUDGET_SEC:
            _log("timeout after fast stage"); return self._emit_fallback(fast,uni)
        pre=sorted(fast.items(),key=lambda kv:kv[1][1],reverse=True)
        finals={c for c,_ in pre[:max(60,min(PREFINAL_TOPK,len(pre)))]}
        _log(f"finalists={len(finals)}")
        res=[]; c2={r.code:r for r in uni}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for f in as_completed({ex.submit(_build_deep,c2[c]):c for c in finals if c in c2}):
                if time.time()-start>HARD_BUDGET_SEC:
                    _log("timeout during deep stage"); break
                try:
                    it=f.result(timeout=15)
                    if it: res.append(it)
                except: pass
        res.sort(key=lambda x:x.get("score",0),reverse=True)
        top=res[:10]
        if not top: return self._emit_fallback(fast,uni)
        meta={"ts":_now_jst().isoformat(timespec="seconds"),
              "mode":DEFAULT_MODE,"horizon":DEFAULT_HORIZON,
              "tone":DEFAULT_TONE,"version":"v4.2-timebudget"}
        text=json.dumps({"meta":meta,"items":top},ensure_ascii=False,indent=2)
        latest=PICKS_DIR/f"latest_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
        hist=PICKS_DIR/f"{_now_jst():%Y%m%d_%H%M%S}_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
        latest.write_text(text,encoding="utf-8"); hist.write_text(text,encoding="utf-8")
        return top
    # ---- fallback
    def _emit_fallback(self,fast,uni):
        _log("emit fallback top10")
        pre=sorted(fast.items(),key=lambda kv:kv[1][1],reverse=True)
        c2={r.code:r for r in uni}; fb=[]
        for code,(close,fastv) in pre[:10]:
            r=c2.get(code,URow(code,code,""))
            entry,tp,sl=round(close*0.996,1),round(close*1.03,1),round(close*0.975,1)
            pl=max(1.0,entry-sl); qty=max(LOT_SIZE,int(TARGET_LOSS_JPY/pl/LOT_SIZE)*LOT_SIZE)
            fb.append(dict(code=r.code,name=r.name,sector=r.sector_name or "",price=close,
                score=round(fastv,1),confidence=2.5,entry=entry,tp=tp,sl=sl,qty=qty,
                required_cash=int(entry*qty),
                exp_profit=int((tp-entry)*qty),exp_loss=int((entry-sl)*qty),
                reasons=["暫定: 時間制限で自動生成"]))
        meta={"ts":_now_jst().isoformat(timespec="seconds"),
              "mode":DEFAULT_MODE,"horizon":DEFAULT_HORIZON,
              "tone":DEFAULT_TONE,"version":"v4.2-timebudget-fallback"}
        text=json.dumps({"meta":meta,"items":fb},ensure_ascii=False,indent=2)
        latest=PICKS_DIR/f"latest_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
        hist=PICKS_DIR/f"{_now_jst():%Y%m%d_%H%M%S}_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
        latest.write_text(text,encoding="utf-8"); hist.write_text(text,encoding="utf-8")
        return fb