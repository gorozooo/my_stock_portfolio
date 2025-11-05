from __future__ import annotations
from pathlib import Path
import pandas as pd
from typing import Tuple

def _normalize_code(val) -> str:
    s = str(val).strip()
    if '.' in s:  # "7203.0" → "7203"
        s = s.split('.', 1)[0]
    return s

def build_snapshot_for_date(date_str: str) -> Tuple[int, int, Path]:
    """
    raw/*.csv を結合して snapshots/YYYY-MM-DD/ohlcv.csv を生成
    返り値: (銘柄数, 行数, 出力パス)
    """
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
            # 最小セット補完
            for c in ['code','date','close','volume','name','sector']:
                if c not in df.columns:
                    df[c] = '' if c in ('name','sector') else 0
            # 型整形
            df['code'] = df['code'].map(_normalize_code)
            df['date'] = df['date'].astype(str)
            df['close'] = pd.to_numeric(df['close'], errors='coerce').fillna(0)
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(int)
            parts.append(df[['code','date','close','volume','name','sector']])
        except Exception:
            continue

    if not parts:
        pd.DataFrame(columns=['code','date','close','volume','name','sector']).to_csv(outp, index=False)
        return 0, 0, outp

    big = pd.concat(parts, ignore_index=True)
    # 重複日付は後勝ち
    big = big.sort_values(['code','date']).drop_duplicates(subset=['code','date'], keep='last')
    big.to_csv(outp, index=False)

    return big['code'].nunique(), len(big), outp