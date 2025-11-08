# -*- coding: utf-8 -*-
"""
picks_build (TEST300)
流動性フィルタをスキップしてテスト用スナップショットを生成
"""
from __future__ import annotations
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample

# 基本設定
MAX_WORKERS = 8
MIN_BARS = 60
LOT_SIZE = 100
MEDIA_ROOT = Path(getattr(settings, "MEDIA_ROOT", "media"))
PICKS_DIR = MEDIA_ROOT / "aiapp" / "picks"
PICKS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_HORIZON = "short"
DEFAULT_MODE = "aggressive"
DEFAULT_TONE = "friendly"
LOCK_PATH = PICKS_DIR / ".picks_build.lock"
JST = timezone(timedelta(hours=9))

def _now_jst(): return datetime.now(JST)
def _log(msg): print(f"[picks_build] {msg}", flush=True)

class BuildLock:
    def __init__(self, path, force=False): self.path=path;self.force=force;self.acquired=False
    def __enter__(self):
        if self.path.exists() and not self.force:
            _log("another build running; exit 202");sys.exit(202)
        self.path.write_text(str(os.getpid()));self.acquired=True;return self
    def __exit__(self,a,b,c):
        if self.acquired: self.path.unlink(missing_ok=True)

@dataclass
class UniverseRow:
    code:str; name:str; sector_name:Optional[str]=None

def _iter_universe(limit=None)->List[UniverseRow]:
    qs=StockMaster.objects.all().values_list("code","name","sector_name")
    rows=[]
    for code,name,sec in qs:
        rows.append(UniverseRow(str(code),str(name),sec or None))
        if limit and len(rows)>=limit:break
    return rows

def _confidence_from_feat(feat_df):
    try:
        last=feat_df.iloc[-1]
        atr_pct=float(last.get("atr_pct",0.02))
        nan_rate=float((feat_df.isna().sum(axis=1).iloc[-60:]>0).mean())
        base=3.0
        if atr_pct<0.03:base+=0.5
        if nan_rate<0.05:base+=0.5
        return float(max(1.0,min(5.0,round(base,2))))
    except: return 2.5

def _build_one(row:UniverseRow)->Optional[Dict[str,Any]]:
    try:
        df=get_prices(row.code,180)
        if df is None or len(df)<MIN_BARS:return None
        feat=compute_features(df)
        if feat is None or len(feat)==0:return None
        score=float(score_sample(feat,mode=DEFAULT_MODE,horizon=DEFAULT_HORIZON))
        score=max(0.0,min(100.0,score))
        conf=_confidence_from_feat(feat)
        close=float(df["close"].iloc[-1])
        entry=round(close*0.994,1);tp=round(close*1.045,1);sl=round(close*0.965,1)
        per_loss=max(1.0,entry-sl);qty=LOT_SIZE
        return{
            "code":row.code,"name":row.name,"sector":row.sector_name or "",
            "price":close,"score":round(score,1),"confidence":round(conf,2),
            "entry":entry,"tp":tp,"sl":sl,"qty":qty,
            "required_cash":int(entry*qty),
            "exp_profit":int((tp-entry)*qty),"exp_loss":int((entry-sl)*qty),
            "reasons":[f"RSI={feat['rsi'].iloc[-1]:.0f}",
                       f"MACD={feat.get('macd_hist',[0])[-1]:+.3f}",
                       f"VWAP乖離={feat.get('vwap_diff_pct',[0])[-1]:+.2%}"]
        }
    except: return None

class Command(BaseCommand):
    help="Build AI picks snapshot (test300, skip liquidity filter)"
    def add_arguments(self,parser):
        parser.add_argument("--force",action="store_true")
        parser.add_argument("--sample",type=int,default=300)
    def handle(self,*a,**o):
        force=bool(o.get("force",False))
        sample=o.get("sample",300)
        start=time.time();_log(f"start sample={sample}")
        with BuildLock(LOCK_PATH,force=force):
            items=self._build_snapshot(sample)
        _log(f"done items={len(items)} dur={round(time.time()-start,1)}s")
    def _build_snapshot(self,sample):
        uni=_iter_universe(limit=sample)
        _log(f"universe={len(uni)} (liquidity skipped)")
        results=[]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs={ex.submit(_build_one,r):r for r in uni}
            for f in as_completed(futs):
                try:
                    itm=f.result()
                    if itm:results.append(itm)
                except:pass
        results.sort(key=lambda x:x["score"],reverse=True)
        topn=results[:10]
        meta={"ts":_now_jst().isoformat(timespec='seconds'),"mode":DEFAULT_MODE,
              "horizon":DEFAULT_HORIZON,"tone":DEFAULT_TONE,
              "universe":len(uni),"version":"picks-v3.2-test300"}
        latest=PICKS_DIR/f"latest_{DEFAULT_HORIZON}_{DEFAULT_MODE}.json"
        snap={"meta":meta,"items":topn}
        latest.write_text(json.dumps(snap,ensure_ascii=False,indent=2),encoding="utf-8")
        return topn