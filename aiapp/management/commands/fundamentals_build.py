# -*- coding: utf-8 -*-
"""
Fundamentals Build（A/B用：Hybridの“ファンダメンタル側”材料をJSON化）

目的:
- picks_build（テクニカル）とは別に、市場の空気（指数/先物など）を “材料JSON” として保存する
- 後段の policy_build / picks_build_hybrid がこのJSONを読んで
  「今はリスク落とす」「今は強気」などの判断材料にする

いまは「仮JSONで動かす」前提なので、まずは市場コンテキスト（指数/先物）を確実に出す。
- Nikkei 225 index: ^N225
- Nikkei 225 futures: NIY=F

出力:
- media/aiapp/fundamentals/latest_fundamentals.json
- media/aiapp/fundamentals/{timestamp}_fundamentals.json

重要（今回の修正点）:
- Django管理コマンドとして動くように `class Command(BaseCommand)` を必ず定義する
  → `python manage.py fundamentals_build` が動く
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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

    asof = datetime.now(JST).isoformat()
    out: Dict[str, Any] = {
        "asof": asof,
        "series": {},
        "errors": {},
    }

    for sym in symbols:
        r = _fetch_yahoo_last2(sym)
        if not r.get("ok"):
            out["errors"][sym] = r.get("error") or "unknown_error"
            out["series"][sym] = asdict(MarketSeries(symbol=sym, updated_at=asof))
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
            updated_at=asof,
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


def build_payload(nbars_hint: int = 30) -> Dict[str, Any]:
    """
    fundamentals_build が吐く JSON 全体を組み立てる。
    将来ここに：
    - 政策/政治/社会情勢（ニュース要約→スコア化）
    - 金利/為替/コモディティ
    - セクター景気循環
    などを追加していく。
    """
    now_iso = datetime.now(JST).isoformat()
    payload: Dict[str, Any] = {
        "meta": {
            "engine": "fundamentals_build",
            "asof": now_iso,
            "nbars_hint": int(nbars_hint),
            "note": "This is a lightweight fundamentals context JSON for hybrid A/B.",
        },
        "market_context": build_market_context(),
    }
    return payload


class Command(BaseCommand):
    help = "Fundamentals Build（指数/先物などの市場コンテキストをJSON化）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--nbars",
            type=int,
            default=_env_int("AIAPP_FUND_NBARS", 30),
            help="将来拡張用のヒント値（現状はpayloadのmetaに入れるだけ）",
        )

    def handle(self, *args, **options):
        nbars = int(options.get("nbars") or _env_int("AIAPP_FUND_NBARS", 30))

        payload = build_payload(nbars_hint=nbars)
        emit_json(payload)

        out_path = OUT_DIR / "latest_fundamentals.json"
        self.stdout.write(self.style.SUCCESS(f"[fundamentals_build] wrote: {out_path}"))