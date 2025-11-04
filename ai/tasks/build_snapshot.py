from __future__ import annotations
from pathlib import Path
import pandas as pd
from typing import Tuple

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
        # 空でも出力（後続のヘルスチェックで検出する）
        pd.DataFrame(columns=['code','date','close','volume','name','sector']).to_csv(outp, index=False)
        return 0, 0, outp

    parts = []
    for f in files:
        try:
            df = pd.read_csv(f)
            # カラム正規化
            cols = {c.lower(): c for c in df.columns}
            # rename to lower
            df.columns = [c.lower() for c in df.columns]
            # date列統一
            if 'date' not in df.columns and 'Date' in cols:
                df = df.rename(columns={'Date':'date'})
            # 最小セット
            need = ['code','date','close','volume','name','sector']
            for c in need:
                if c not in df.columns:
                    df[c] = '' if c in ('name','sector') else 0
            parts.append(df[need])
        except Exception:
            # 壊れたファイルはスキップ
            continue

    if not parts:
        pd.DataFrame(columns=['code','date','close','volume','name','sector']).to_csv(outp, index=False)
        return 0, 0, outp

    big = pd.concat(parts, ignore_index=True)
    big = big.dropna(subset=['code','date'])
    big['date'] = big['date'].astype(str)
    big = big.sort_values(['code','date'])
    big.to_csv(outp, index=False)

    n_codes = big['code'].nunique()
    n_rows = len(big)
    return n_codes, n_rows, outp