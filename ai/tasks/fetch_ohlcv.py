from __future__ import annotations
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List, Tuple, Dict
from datetime import datetime
import traceback

# 依存: yfinance, pandas, numpy
import yfinance as yf
import pandas as pd

UNIVERSE_FALLBACK = ['7203','9432','8035','6758','9984']

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def jp_symbol(code: str) -> str:
    """Yahooの日本株ティッカーに変換（例: 7203 -> 7203.T）"""
    code = code.strip()
    return code if '.' in code else f"{code}.T"

def load_universe() -> List[Tuple[str, str, str]]:
    """
    ユニバースを返す: [(code, name, sector)]
    優先度:
      1) media/universe/codes.csv (code,name,sector)
      2) media/universe/codes.txt (codeのみ)
      3) DB TrendResult 既存codes
      4) フォールバック
    """
    root = Path('media/universe')
    csvp = root/'codes.csv'
    txtp = root/'codes.txt'
    out: List[Tuple[str,str,str]] = []
    if csvp.exists():
        df = pd.read_csv(csvp)
        for _, r in df.iterrows():
            out.append((str(r['code']), str(r.get('name','')), str(r.get('sector',''))))
        if out: return out
    if txtp.exists():
        codes = [c.strip() for c in txtp.read_text(encoding='utf-8').splitlines() if c.strip()]
        if codes:
            return [(c,'','') for c in codes]
    # DB（任意）
    try:
        from ai.models import TrendResult
        qs = TrendResult.objects.values_list('code','name','sector_jp').order_by('code')
        out = [(c or '', n or '', s or '') for c,n,s in qs]
        if out: return out
    except Exception:
        pass
    return [(c,'','') for c in UNIVERSE_FALLBACK]

def fetch_one(code: str, name: str, sector: str, out_dir: Path, as_of: str) -> Tuple[str, bool, str]:
    """
    単一銘柄を取得して raw/<code>.csv に追記更新
    CSV: code,date,close,volume,name,sector
    """
    sym = jp_symbol(code)
    try:
        # 直近3ヶ月を取得（欠損や権利落ちの補足に十分・軽量）
        df = yf.download(sym, period='3mo', interval='1d', auto_adjust=False, progress=False)
        if df is None or df.empty:
            return code, False, "empty"
        df = df[['Close','Volume']].reset_index(names='Date')
        df['Date'] = pd.to_datetime(df['Date']).dt.date.astype(str)
        df = df.rename(columns={'Close':'close','Volume':'volume'})
        df['code'] = code
        df['name'] = name or ''
        df['sector'] = sector or ''

        # 既存ファイルをマージ更新（重複日付は置換）
        fp = out_dir/f"{code}.csv"
        if fp.exists():
            prev = pd.read_csv(fp)
            merged = pd.concat([prev, df[['code','Date','close','volume','name','sector']]], ignore_index=True)
            merged = merged.drop_duplicates(subset=['code','Date'], keep='last').sort_values('Date')
        else:
            merged = df[['code','Date','close','volume','name','sector']].sort_values('Date')

        merged.to_csv(fp, index=False)
        return code, True, "ok"
    except Exception as e:
        return code, False, f"err:{e}"

def fetch_all(as_of: str, workers: int = 5) -> Dict[str, List[str]]:
    uni = load_universe()
    raw_dir = Path('media/ohlcv/raw')
    ensure_dir(raw_dir)
    fail_dir = Path('media/ohlcv/failures')
    ensure_dir(fail_dir)
    fail_log = fail_dir/f"{as_of}.txt"

    successes: List[str] = []
    failures: List[str] = []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch_one, code, name, sector, raw_dir, as_of) for code,name,sector in uni]
        for fut in as_completed(futs):
            code, ok, info = fut.result()
            if ok:
                successes.append(code)
            else:
                failures.append(code)

    if failures:
        fail_log.write_text("\n".join(failures), encoding='utf-8')

    return {"ok": successes, "ng": failures}