# aiapp/management/commands/fundamentals_build.py
# -*- coding: utf-8 -*-
"""
これは何のファイル？
- Django管理コマンド `python manage.py fundamentals_build` の本体。
- Hybrid（テクニカル×ファンダ×政策）の “ファンダ側材料” を JSON に書き出す。

出力:
- media/aiapp/fundamentals/latest_fundamentals.json
- media/aiapp/fundamentals/{timestamp}_fundamentals.json

今回の対応:
- JGB10Y=RR（日本10年金利）だけ Yahoo 側がコケやすいので、
  まず yfinance を試し、ダメなら YCharts をスクレイピングして埋める。
  （MarketWatchは環境/ブロックで取りづらいことがあるので、まずは確実に動くルートを優先）
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from django.core.management.base import BaseCommand

JST = timezone(timedelta(hours=9))

OUT_DIR = Path("media/aiapp/fundamentals")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def dt_now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def _env_int(key: str, default: int) -> int:
    v = os.getenv(key)
    if v is None:
        return default
    try:
        return int(v)
    except Exception:
        return default


def _http_get_text(url: str, timeout: int = 12) -> Tuple[bool, str, Optional[str]]:
    """
    (ok, text, error)
    """
    try:
        import requests  # type: ignore
    except Exception:
        return False, "", "requests_not_available"

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return False, "", f"http_status:{r.status_code}"
        return True, r.text, None
    except Exception as ex:
        return False, "", f"http_error:{ex}"


@dataclass
class MarketSeries:
    symbol: str
    last: Optional[float] = None
    prev: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    updated_at: Optional[str] = None  # ISO
    source: Optional[str] = None      # "yfinance" / "ycharts" etc


def _fetch_yahoo_last2(symbol: str) -> Dict[str, Any]:
    """
    yfinance で直近2本（終値）を拾って last/prev を作る。
    落ちても上位で握って “欠損でも動く” 前提。
    """
    try:
        import yfinance as yf  # type: ignore
    except Exception:
        return {"ok": False, "error": "yfinance_not_available"}

    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="10d", interval="1d")
        if hist is None or len(hist) < 1:
            return {"ok": False, "error": "empty_history"}

        closes = []
        for _, row in hist.tail(4).iterrows():
            c = row.get("Close", None)
            fv = _safe_float(c)
            if fv is not None:
                closes.append(fv)

        if not closes:
            return {"ok": False, "error": "no_close"}

        last = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else None
        return {"ok": True, "last": last, "prev": prev, "source": "yfinance"}

    except Exception as ex:
        return {"ok": False, "error": f"yfinance_error:{ex}"}


def _fetch_jgb10y_from_ycharts() -> Dict[str, Any]:
    """
    YCharts: "Japan 10 Year Government Bond Interest Rate is at X%"
    から X を抜く（% → 数値のまま返す）。
    """
    url = "https://ycharts.com/indicators/japan_10_year_government_bond_interest_rate"
    ok, text, err = _http_get_text(url)
    if not ok:
        return {"ok": False, "error": f"ycharts_fetch_failed:{err}", "source": "ycharts"}

    # 例: "... is at 2.18%, compared to ..."
    m = re.search(r"is at\s+([0-9]+(?:\.[0-9]+)?)\s*%", text)
    if not m:
        return {"ok": False, "error": "ycharts_parse_failed", "source": "ycharts"}

    last = _safe_float(m.group(1))
    if last is None:
        return {"ok": False, "error": "ycharts_value_nan", "source": "ycharts"}

    # prev はページから拾える時もあるが、壊れやすいので今回は無理に作らない
    return {"ok": True, "last": last, "prev": None, "source": "ycharts"}


def _build_one_series(sym: str, asof_iso: str, *, special_jgb10y: bool = False) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    returns: (series_dict, error_str)
    """
    if special_jgb10y:
        r = _fetch_yahoo_last2(sym)
        if not r.get("ok"):
            # Yahooがダメなら YCharts へ
            r2 = _fetch_jgb10y_from_ycharts()
            if not r2.get("ok"):
                # 両方ダメ
                ms = MarketSeries(symbol=sym, updated_at=asof_iso, source="none")
                return asdict(ms), (r2.get("error") or r.get("error") or "unknown_error")
            r = r2

        last = _safe_float(r.get("last"))
        prev = _safe_float(r.get("prev"))

        change = None
        change_pct = None
        if last is not None and prev is not None and prev != 0:
            change = last - prev
            change_pct = (last - prev) / prev * 100.0

        ms = MarketSeries(
            symbol=sym,
            last=last,
            prev=prev,
            change=_safe_float(change),
            change_pct=_safe_float(change_pct),
            updated_at=asof_iso,
            source=str(r.get("source") or "unknown"),
        )
        return asdict(ms), None

    # 通常ルート（yfinance）
    r = _fetch_yahoo_last2(sym)
    if not r.get("ok"):
        ms = MarketSeries(symbol=sym, updated_at=asof_iso, source="yfinance")
        return asdict(ms), (r.get("error") or "unknown_error")

    last = _safe_float(r.get("last"))
    prev = _safe_float(r.get("prev"))

    change = None
    change_pct = None
    if last is not None and prev is not None and prev != 0:
        change = last - prev
        change_pct = (last - prev) / prev * 100.0

    ms = MarketSeries(
        symbol=sym,
        last=last,
        prev=prev,
        change=_safe_float(change),
        change_pct=_safe_float(change_pct),
        updated_at=asof_iso,
        source=str(r.get("source") or "yfinance"),
    )
    return asdict(ms), None


def build_market_context() -> Dict[str, Any]:
    """
    市場コンテキスト（指数/先物/為替/金利など）。
    “取れないものがあっても落ちない” を最優先にする。
    """
    symbols = [
        "^N225",       # 日経平均
        "NIY=F",       # 日経225先物
        "USDJPY=X",    # USDJPY
        "DX-Y.NYB",    # ドルインデックス
        "^TNX",        # 米10年金利（%）
        "JGB10Y=RR",   # ★日本10年（ここだけ特別扱い）
    ]

    asof_iso = datetime.now(JST).isoformat()

    out: Dict[str, Any] = {
        "asof": asof_iso,
        "series": {},
        "errors": {},
        "notes": {},
    }

    for sym in symbols:
        is_jgb10y_special = (sym == "JGB10Y=RR")

        s, err = _build_one_series(sym, asof_iso, special_jgb10y=is_jgb10y_special)
        out["series"][sym] = s

        if err:
            out["errors"][sym] = err

        if is_jgb10y_special:
            out["notes"]["jgb10y_selected"] = s.get("source")

    return out


def emit_json(payload: Dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    latest = OUT_DIR / "latest_fundamentals.json"
    stamped = OUT_DIR / f"{dt_now_stamp()}_fundamentals.json"

    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    latest.write_text(s, encoding="utf-8")
    stamped.write_text(s, encoding="utf-8")


class Command(BaseCommand):
    help = "Fundamentals Build（Hybrid用の“材料JSON”を生成）"

    def handle(self, *args, **opts):
        nbars = _env_int("AIAPP_FUND_NBARS", 30)

        payload: Dict[str, Any] = {
            "meta": {
                "engine": "fundamentals_build",
                "asof": datetime.now(JST).isoformat(),
                "nbars_hint": nbars,
                "note": "This is a lightweight fundamentals context JSON for hybrid A/B.",
            },
            "market_context": build_market_context(),
            # 将来拡張:
            # - 政策/政治/社会（ニュース要約→スコア）
            # - セクター循環
            # - クレジットスプレッド、コモディティ等
        }

        emit_json(payload)

        errors = list((payload.get("market_context") or {}).get("errors", {}).keys())
        self.stdout.write(self.style.SUCCESS("[fundamentals_build] wrote: media/aiapp/fundamentals/latest_fundamentals.json"))
        if errors:
            self.stdout.write(self.style.WARNING(f"[fundamentals_build] warnings: errors={errors}"))