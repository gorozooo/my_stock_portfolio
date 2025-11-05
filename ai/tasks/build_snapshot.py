from __future__ import annotations
from pathlib import Path
import pandas as pd
from typing import Tuple

def _norm_code(x):
    s = str(x).strip()
    return s.split('.')[0]  # ← 「.0」を除去

def build_snapshot_for_date(date_str: str) -> Tuple[int, int, Path]:
    raw = Path('media/ohlcv/raw')
    snap_dir = Path('media/ohlcv/snapshots')/date_str
    snap_dir.mkdir(parents=True, exist_ok=True)
    outp = snap_dir/'ohlcv.csv'

    files = list(raw.glob('*.csv'))
    if not files:
        pd.DataFrame(columns=['code','date','close','volume','name','sector']).to_csv(outp, index=False)
        return 0, 0, outp

    parts = []
    for f in files:
        try:
            df = pd.read_csv(f)
            df.columns = [c.lower() for c in df.columns]
            if 'date' not in df.columns and 'Date' in df.columns:
                df = df.rename(columns={'Date':'date'})
            for c in ['code','date','close','volume','name','sector']:
                if c not in df.columns:
                    df[c] = '' if c in ('name','sector') else 0
            df['code'] = df['code'].apply(_norm_code)  # ★正規化
            parts.append(df[['code','date','close','volume','name','sector']])
        except Exception:
            continue

    if not parts:
        pd.DataFrame(columns=['code','date','close','volume','name','sector']).to_csv(outp, index=False)
        return 0, 0, outp

    big = pd.concat(parts, ignore_index=True)
    big = big.dropna(subset=['code','date'])
    big['date'] = big['date'].astype(str)

    # 同日・同コードは最後を採用
    big = big.sort_values(['code','date']).drop_duplicates(['code','date'], keep='last')
    big.to_csv(outp, index=False)

    return big['code'].nunique(), len(big), outp