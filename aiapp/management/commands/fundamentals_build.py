# fundamentals_build.py
# -*- coding: utf-8 -*-
"""
Fundamentals Build（A/B用：Hybridの“ファンダメンタル側”材料をJSON化）

いまは「仮JSONで動かす」前提なので、まずは市場コンテキスト（指数/先物）を確実に出す。
- Nikkei 225 index: ^N225
- Nikkei 225 futures: NIY=F

出力:
- media/aiapp/fundamentals/latest_fundamentals.json
- media/aiapp/fundamentals/{timestamp}_fundamentals.json

設計意図（初心者向けに超ざっくり）:
- picks_build（テクニカル）とは別に、相場の空気（指数/先物）を “材料” として保存しておく
- 後段の policy_build / picks_build_hybrid がこのJSONを読んで
  「今はリスク落とす」「今は強気」みたいな判断材料にする
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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


@dataclass
class MarketSeries:
    symbol: str
    last: Optional[float] = None
    prev: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    updated_at: Optional[str] = None  # ISO


def _fetch_yahoo_last2(symbol: str) -> Dict[str, Any]:
    """
    Yahoo Finance 系（yfinance）から直近2本を取って、last/prevを作る。
    ※ yfinance が無い/落ちても、上位で握って “欠損でも動く” ようにする。
    """
    try:
        import yfinance as yf  # type: ignore
    except Exception:
        return {"ok": False, "error": "yfinance_not_available"}

    try:
        # 直近数日を取りに行く（営業日ズレに強い）
        t = yf.Ticker(symbol)
        hist = t.history(period="7d", interval="1d")
        if hist is None or len(hist) < 1:
            return {"ok": False, "error": "empty_history"}

        # Close列から末尾2つ（あれば）
        closes = []
        for _, row in hist.tail(3).iterrows():
            c = row.get("Close", None)
            fv = _safe_float(c)
            if fv is not None:
                closes.append(fv)

        if not closes:
            return {"ok": False, "error": "no_close"}

        last = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else None
        return {"ok": True, "last": last, "prev": prev}

    except Exception as ex:
        return {"ok": False, "error": f"yfinance_error:{ex}"}


def build_market_context() -> Dict[str, Any]:
    """
    市場コンテキスト（指数/先物など）。
    まずは “日経平均/日経先物” を最優先で載せる。
    """
    # Yahoo Finance の一般的な参照シンボル
    # - Nikkei 225 index: ^N225
    # - Nikkei 225 futures: NIY=F
    symbols = [
        "^N225",   # 日経平均
        "NIY=F",   # 日経225先物（代表的に参照されるシンボル）
    ]

    out: Dict[str, Any] = {
        "asof": datetime.now(JST).isoformat(),
        "series": {},
        "errors": {},
    }

    for sym in symbols:
        r = _fetch_yahoo_last2(sym)
        if not r.get("ok"):
            out["errors"][sym] = r.get("error") or "unknown_error"
            out["series"][sym] = asdict(MarketSeries(symbol=sym, updated_at=out["asof"]))
            continue

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
            updated_at=out["asof"],
        )
        out["series"][sym] = asdict(ms)

    return out


def emit_json(payload: Dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    latest = OUT_DIR / "latest_fundamentals.json"
    stamped = OUT_DIR / f"{dt_now_stamp()}_fundamentals.json"

    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    latest.write_text(s, encoding="utf-8")
    stamped.write_text(s, encoding="utf-8")


def main() -> int:
    nbars = _env_int("AIAPP_FUND_NBARS", 30)

    payload: Dict[str, Any] = {
        "meta": {
            "engine": "fundamentals_build",
            "asof": datetime.now(JST).isoformat(),
            "nbars_hint": nbars,
            "note": "This is a lightweight fundamentals context JSON for hybrid A/B.",
        },
        "market_context": build_market_context(),
        # ここに将来:
        # - 政策/政治/社会情勢（ニュース要約→スコア化）
        # - 金利/為替/コモディティ
        # - セクター景気循環
        # を足していく
    }

    emit_json(payload)
    print("[fundamentals_build] wrote:", str(OUT_DIR / "latest_fundamentals.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())