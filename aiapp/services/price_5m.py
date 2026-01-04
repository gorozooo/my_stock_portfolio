# aiapp/services/price_5m.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf
from django.conf import settings
from django.utils import timezone


@dataclass
class Bar5m:
    """
    5分足1本ぶんのシンプルなデータモデル。
    ts   : 日本時間(JST)のタイムスタンプ
    open : 始値
    high : 高値
    low  : 安値
    close: 終値
    volume: 出来高
    """
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


# 保存先: media/aiapp/prices/5m/{code}/{yyyymmdd}.jsonl
BASE_DIR = Path(settings.MEDIA_ROOT) / "aiapp" / "prices" / "5m"
BASE_DIR.mkdir(parents=True, exist_ok=True)


def _to_jst(dt: datetime) -> datetime:
    """
    任意の datetime をアプリのデフォルトタイムゾーン（JST想定）にそろえる。
    """
    if timezone.is_aware(dt):
        return timezone.localtime(dt)
    return timezone.make_aware(dt, timezone.get_default_timezone())


def _code_to_yf_symbol(code: str) -> str:
    """
    日本株コードを yfinance 用シンボルに変換。
    例) "7203" -> "7203.T"
    """
    code = str(code).strip()
    if not code:
        raise ValueError("code is empty")
    if code.endswith(".T"):
        return code
    return f"{code}.T"


def _daily_path(code: str, d: date) -> Path:
    day_str = d.strftime("%Y%m%d")
    return BASE_DIR / code / f"{day_str}.jsonl"


def load_bars_from_file(code: str, d: date) -> List[Bar5m]:
    """
    既に保存済みの 5分足ファイルがあれば、それを読み込んで Bar5m のリストとして返す。
    無ければ空リスト。
    """
    path = _daily_path(code, d)
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
            obj = json.loads(line)
        except Exception:
            continue

        try:
            ts_str = obj.get("ts")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str)
            ts = _to_jst(ts)

            bars.append(
                Bar5m(
                    ts=ts,
                    open=float(obj.get("open")),
                    high=float(obj.get("high")),
                    low=float(obj.get("low")),
                    close=float(obj.get("close")),
                    volume=float(obj.get("volume") or 0.0),
                )
            )
        except Exception:
            continue

    # 念のため時系列順にソート
    bars.sort(key=lambda b: b.ts)
    return bars


def fetch_and_save_bars(code: str, d: date) -> List[Bar5m]:
    """
    yfinance から指定日の 5分足を取得し、JSONL として保存した上で Bar5m リストを返す。
    """
    yf_symbol = _code_to_yf_symbol(code)
    # yfinance は start <= t < end で返す
    start = datetime(d.year, d.month, d.day)
    end = start + timedelta(days=1)

    try:
        df = yf.download(
            yf_symbol,
            interval="5m",
            start=start,
            end=end,
            auto_adjust=False,
            progress=False,
        )
    except Exception as e:
        # 取得失敗時は空で返す（後続で no_position 判定などに使う想定）
        print(f"[price_5m] fetch failed for {code} {d}: {e}")
        return []

    if df is None or df.empty:
        return []

    # index を JST にそろえる
    idx = df.index
    tz = getattr(idx, "tz", None)
    jst = timezone.get_default_timezone()
    if tz is not None:
        idx = idx.tz_convert(jst)
    else:
        idx = idx.tz_localize(jst)

    df = df.copy()
    df.index = idx

    bars: List[Bar5m] = []
    for ts, row in df.iterrows():
        try:
            o = float(row["Open"])
            h = float(row["High"])
            l = float(row["Low"])
            c = float(row["Close"])
            v = float(row.get("Volume") or 0.0)
        except Exception:
            continue

        bars.append(
            Bar5m(ts=_to_jst(ts), open=o, high=h, low=l, close=c, volume=v)
        )

    # 保存
    path = _daily_path(code, d)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as f:
            for b in bars:
                rec = {
                    "ts": _to_jst(b.ts).isoformat(),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[price_5m] failed to write {path}: {e}")

    return bars


def get_5m_bars(code: str, d: date, refresh: bool = False) -> List[Bar5m]:
    """
    指定銘柄コード・日付の 5分足を取得する。

    - refresh=False のとき:
        1) ローカルファイルがあればそれを読み込む
        2) 無ければ yfinance から取得して保存
    - refresh=True のとき:
        常に yfinance から取り直して上書き保存
    """
    code = str(code).strip()
    if not code:
        return []

    if not refresh:
        cached = load_bars_from_file(code, d)
        if cached:
            return cached

    return fetch_and_save_bars(code, d)


def get_5m_bars_range(
    code: str,
    start_date: date,
    horizon_days: int = 5,
    refresh: bool = False,
) -> List[Bar5m]:
    """
    start_date から horizon_days 営業日分（カレンダー上の日数）の 5分足をまとめて取得。

    - 1日ずつ get_5m_bars を呼ぶ
    - 取得した Bar5m を時系列順にマージして返す
    """
    bars: List[Bar5m] = []
    for i in range(horizon_days):
        d = start_date + timedelta(days=i)
        daily = get_5m_bars(code, d, refresh=refresh)
        if not daily:
            continue
        bars.extend(daily)

    bars.sort(key=lambda b: b.ts)
    return bars


def cleanup_old_files(retention_days: int = 15) -> None:
    """
    retention_days より古い 5分足ファイルを削除する。
    例: retention_days=15 なら 15日前より古い日付のファイルを削除。
    """
    now = timezone.localtime().date()
    threshold = now - timedelta(days=retention_days)

    if not BASE_DIR.exists():
        return

    for code_dir in BASE_DIR.iterdir():
        if not code_dir.is_dir():
            continue

        for path in code_dir.glob("*.jsonl"):
            name = path.stem  # "20251126"
            try:
                d = datetime.strptime(name, "%Y%m%d").date()
            except Exception:
                # 変な名前のファイルはスキップ
                continue

            if d < threshold:
                try:
                    path.unlink()
                    # 空ディレクトリになったら削除してもよいが、そこまでは必須でないので任意
                except Exception:
                    continue