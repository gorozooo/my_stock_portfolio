# aiapp/management/commands/fundamentals_build.py
# -*- coding: utf-8 -*-
"""
Fundamentals Build（A/B用：Hybridの“ファンダメンタル側”材料をJSON化）

目的:
- picks_build（テクニカル）とは別に、相場の空気（指数/先物/為替/金利など）を “材料” として保存しておく
- 後段の policy_build / picks_build_hybrid がこのJSONを読んで
  「今はリスク落とす」「今は強気」みたいな判断材料にする

出力:
- media/aiapp/fundamentals/latest_fundamentals.json
- media/aiapp/fundamentals/{timestamp}_fundamentals.json

今回追加するもの（できるだけ確実に取れる系）:
- Nikkei 225 index        : ^N225
- Nikkei 225 futures      : NIY=F
- USD/JPY                 : USDJPY=X
- US Dollar Index (DXY)   : DX-Y.NYB
- US 10Y Yield (CBOE TNX) : ^TNX

日本10年金利（JGB10Y）について:
- Yahoo/yfinance のシンボルが環境で取れない場合が多いので
  “候補を試して取れたものだけ採用” し、取れなければ errors に載せる。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

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
    Yahoo Finance 系（yfinance）から直近2本を取って last/prev を作る。
    - yfinance が無い/落ちても “欠損でも動く”
    - period は数日分を取り、営業日ズレを吸収
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

        closes: List[float] = []
        # 念のため tail 多め
        for _, row in hist.tail(5).iterrows():
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


def _mk_series(sym: str, asof_iso: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    sym を取って MarketSeries dict を返す。
    失敗時は “空の series + error文字列” を返す。
    """
    r = _fetch_yahoo_last2(sym)
    if not r.get("ok"):
        ms = MarketSeries(symbol=sym, updated_at=asof_iso)
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
    )
    return asdict(ms), None


def build_market_context() -> Dict[str, Any]:
    """
    市場コンテキスト（指数/先物/為替/金利など）。
    “取れなくても落ちない” のが最優先。
    """
    asof = datetime.now(JST).isoformat()

    # まずは確実性が高いところを固定で
    base_symbols = [
        "^N225",      # 日経平均
        "NIY=F",      # 日経225先物（代表）
        "USDJPY=X",   # ドル円
        "DX-Y.NYB",   # ドル指数（DXY）
        "^TNX",       # 米10年金利（CBOE TNX）
    ]

    # 日本10年金利（環境で取れない可能性があるので候補を試す）
    # 取れたものだけ “採用扱い” にする
    jgb10y_candidates = [
        "JP10Y=RR",
        "^JP10Y",
        "JPY10Y=RR",
        "JGB10Y=RR",
    ]

    out: Dict[str, Any] = {
        "asof": asof,
        "series": {},
        "errors": {},
        "notes": {
            "jgb10y_candidates": jgb10y_candidates,
            "hint": "series は Yahoo/yfinance のシンボルで last/prev を出す。取れないものは errors に入る。",
        },
    }

    # base は全部試す（失敗しても errors に落とすだけ）
    for sym in base_symbols:
        s, err = _mk_series(sym, asof)
        out["series"][sym] = s
        if err:
            out["errors"][sym] = err

    # JGB10Y は “取れた最初の1つ” を使う（取れなければ全部 errors へ）
    jgb_ok = None
    for sym in jgb10y_candidates:
        s, err = _mk_series(sym, asof)
        out["series"][sym] = s
        if not err:
            jgb_ok = sym
            break
        out["errors"][sym] = err

    out["notes"]["jgb10y_selected"] = jgb_ok  # None なら取れてない
    return out


def emit_json(payload: Dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    latest = OUT_DIR / "latest_fundamentals.json"
    stamped = OUT_DIR / f"{dt_now_stamp()}_fundamentals.json"

    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    latest.write_text(s, encoding="utf-8")
    stamped.write_text(s, encoding="utf-8")


def build_payload() -> Dict[str, Any]:
    nbars = _env_int("AIAPP_FUND_NBARS", 30)

    payload: Dict[str, Any] = {
        "meta": {
            "engine": "fundamentals_build",
            "asof": datetime.now(JST).isoformat(),
            "nbars_hint": nbars,
            "note": "Lightweight fundamentals context JSON for hybrid A/B.",
        },
        "market_context": build_market_context(),
        # 将来ここに追加:
        # - 政策/政治/社会情勢（ニュース要約→スコア化）
        # - 金利/為替/コモディティの拡張
        # - セクター景気循環
    }
    return payload


class Command(BaseCommand):
    help = "Fundamentals Build（Hybrid用：市場コンテキストJSON生成）"

    def handle(self, *args, **opts):
        payload = build_payload()
        emit_json(payload)
        self.stdout.write(self.style.SUCCESS(f"[fundamentals_build] wrote: {OUT_DIR / 'latest_fundamentals.json'}"))
        # 取れなかったものがあれば軽く表示
        errs = (payload.get("market_context") or {}).get("errors") or {}
        if isinstance(errs, dict) and errs:
            self.stdout.write(self.style.WARNING(f"[fundamentals_build] warnings: errors={list(errs.keys())}"))