from __future__ import annotations
from pathlib import Path
import pandas as pd
from typing import Dict, Any, Tuple

def analyze_snapshot(date_str: str) -> Dict[str, Any]:
    snap = Path('media/ohlcv/snapshots')/date_str/'ohlcv.csv'
    fail = Path('media/ohlcv/failures')/f'{date_str}.txt'

    metrics = {
        'date': date_str,
        'snapshot_exists': snap.exists(),
        'codes': 0,
        'rows': 0,
        'missing_codes': 0,
        'invalid_rows': 0,
        'failures': 0,
        'failure_codes': [],
    }

    if not snap.exists():
        if fail.exists():
            lines = [x.strip() for x in fail.read_text(encoding='utf-8').splitlines() if x.strip()]
            metrics['failures'] = len(lines)
            metrics['failure_codes'] = lines[:50]
        return metrics

    try:
        df = pd.read_csv(snap)
    except Exception:
        return metrics

    metrics['rows'] = len(df)
    if len(df) == 0:
        return metrics

    metrics['codes'] = df['code'].nunique()

    # 3行未満（期間が短すぎる）を欠損扱い
    cnt = df.groupby('code').size()
    metrics['missing_codes'] = int((cnt < 3).sum())

    # 非正値チェック
    invalid = df[(df['close'] <= 0) | (df['volume'] < 0)]
    metrics['invalid_rows'] = len(invalid)

    if fail.exists():
        lines = [x.strip() for x in fail.read_text(encoding='utf-8').splitlines() if x.strip()]
        metrics['failures'] = len(lines)
        metrics['failure_codes'] = lines[:50]

    return metrics

def format_ops_lines(metrics: Dict[str, Any]) -> Tuple[str, list]:
    title = f"データヘルス {metrics.get('date')}"
    lines = []
    lines.append(f"snapshot: {'OK' if metrics.get('snapshot_exists') else 'MISSING'}")
    lines.append(f"codes: {metrics.get('codes')}  rows: {metrics.get('rows')}")
    lines.append(f"missing_codes(<3rows): {metrics.get('missing_codes')}")
    lines.append(f"invalid_rows: {metrics.get('invalid_rows')}")
    lines.append(f"fetch_failures: {metrics.get('failures')}")
    if metrics.get('failure_codes'):
        lines.append("failed: " + ", ".join(metrics['failure_codes'][:10]) + (" ..." if len(metrics['failure_codes'])>10 else ""))
    return title, lines