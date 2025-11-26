# aiapp/services/bars_5m.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yfinance as yf
from django.conf import settings
from django.utils import timezone

# キャッシュ保存先: MEDIA_ROOT/aiapp/bars_5m
BARS_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "bars_5m"
BARS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Bar5m:
    ts: timezone.datetime
    open: float
    high: float
    low: float
    close: float


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "null"):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _parse_ts(ts_str: str) -> Optional[timezone.datetime]:
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        dt = timezone.datetime.fromisoformat(ts_str)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_default_timezone())
        return timezone.localtime(dt)
    except Exception:
        return None


def _coerce_bar_from_json(d: Dict[str, Any]) -> Optional[Bar5m]:
    ts = _parse_ts(str(d.get("ts") or ""))
    if ts is None:
        return None

    o = _safe_float(d.get("open"))
    h = _safe_float(d.get("high"))
    l = _safe_float(d.get("low"))
    c = _safe_float(d.get("close"))
    if o is None or h is None or l is None or c is None:
        return None

    return Bar5m(ts=ts, open=o, high=h, low=l, close=c)


def _cache_path_for_code(code: str) -> Path:
    """
    各銘柄ごとに 1ファイル: <MEDIA_ROOT>/aiapp/bars_5m/{code}_5m.jsonl
    例: 7508 -> 7508_5m.jsonl
    """
    code = str(code).strip()
    return BARS_DIR / f"{code}_5m.jsonl"


def _load_from_cache(code: str) -> List[Bar5m]:
    """
    キャッシュファイルを読み込んで Bar5m のリストにして返す。
    ファイルが無ければ空リスト。
    """
    path = _cache_path_for_code(code)
    if not path.exists():
        return []

    bars: List[Bar5m] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        bar = _coerce_bar_from_json(d)
        if bar is not None:
            bars.append(bar)

    # 念のため時刻順にソート
    bars.sort(key=lambda b: b.ts)
    return bars


def _save_to_cache(code: str, bars: List[Bar5m]) -> None:
    """
    Bar5m のリストを丸ごとキャッシュファイルに書き出す。
    （後から追記するよりも、毎回全体を上書きする運用）
    """
    path = _cache_path_for_code(code)
    lines: List[str] = []

    for b in sorted(bars, key=lambda x: x.ts):
        d = {
            "ts": b.ts.isoformat(),
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
        }
        lines.append(json.dumps(d, ensure_ascii=False))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        # キャッシュ失敗しても致命的ではないので握りつぶす
        pass


def _fetch_5m_from_yf(code: str, days: int = 60) -> List[Bar5m]:
    """
    yfinance を使って最大60日分の5分足を取得し、Bar5m のリストで返す。
    - JPX銘柄は "7508.T" のように .T を付けて取得
    - days は 1〜60 の範囲で使うイメージ
    """
    ticker = f"{str(code).strip()}.T"

    try:
        # 60日分まとめて取り、あとで日付でフィルタする
        df = yf.download(
            ticker,
            period=f"{days}d",
            interval="5m",
            progress=False,
        )
    except Exception:
        return []

    bars: List[Bar5m] = []
    if df is None or df.empty:
        return bars

    # df.index は DatetimeIndex
    for ts, row in df.iterrows():
        # ts は pandas.Timestamp
        ts_py = ts.to_pydatetime()
        if timezone.is_naive(ts_py):
            ts_py = timezone.make_aware(ts_py, timezone.get_default_timezone())
        ts_py = timezone.localtime(ts_py)

        o = _safe_float(row.get("Open"))
        h = _safe_float(row.get("High"))
        l = _safe_float(row.get("Low"))
        c = _safe_float(row.get("Close"))
        if o is None or h is None or l is None or c is None:
            continue

        bars.append(Bar5m(ts=ts_py, open=o, high=h, low=l, close=c))

    bars.sort(key=lambda b: b.ts)
    return bars


def get_5m_bars_range(code: str, start_date, horizon_days: int = 5) -> List[Bar5m]:
    """
    外部公開API:

      get_5m_bars_range(code, start_date, horizon_days)

    - まずローカルキャッシュから全5分足を読み込む
    - キャッシュが無かったり極端に少ない場合は yfinance から取得してキャッシュ
    - その上で start_date〜horizon_days 営業日ぶんだけをフィルタして返す

    ※ horizon_days は「exit評価用の期間」で、実際には日付ベースで
       start_date <= ts.date() < start_date + horizon_days
       のものを返す。
    """
    if horizon_days <= 0:
        return []

    # 1. まずキャッシュから読む
    bars_all = _load_from_cache(code)

    # 2. キャッシュが無い or 少なすぎる場合は yfinance から取得してキャッシュ更新
    if len(bars_all) < 10:
        fetched = _fetch_5m_from_yf(code, days=max(horizon_days * 2, 7))
        if fetched:
            bars_all = fetched
            _save_to_cache(code, bars_all)

    if not bars_all:
        return []

    # 3. start_date〜horizon_days分だけフィルタ
    start = start_date
    end = start_date + timezone.timedelta(days=horizon_days)

    filtered: List[Bar5m] = []
    for b in bars_all:
        d = b.ts.date()
        if start <= d < end:
            filtered.append(b)

    filtered.sort(key=lambda b: b.ts)
    return filtered