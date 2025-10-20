# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, time
from datetime import datetime, timezone as dt_tz
from typing import Dict, Any, Optional, List

from django.core.management.base import BaseCommand
from django.conf import settings

import yfinance as yf

# 追加：セクターRSも同じジョブで保存
# latest_sector_strength() はあなたの既存servicesから取得
try:
    from portfolio.services.market import latest_sector_strength
except Exception:
    latest_sector_strength = None  # 環境によっては未導入でも動くように


# ------------ 基本ユーティリティ ------------
def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def _save_json(path: str, data: Any) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ------------ Yahoo Finance ユーティリティ ------------
def _pick_symbol(symbols: List[str]) -> Optional[str]:
    """候補シンボルを順に試し、ヒストリが返る最初のものを採用"""
    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=False)
            if not hist.empty:
                return sym
        except Exception:
            continue
    return None

def _quote_last_and_pctd(sym: str) -> Optional[Dict[str, float]]:
    """
    現値と前日終値比の%を返す。可能なら intraday の最後、無ければ日足終値。
    pct_d は「前日終値比の変化率[%]」を返す。
    """
    try:
        tk = yf.Ticker(sym)

        # 日足で前日終値を取りにいく
        d = tk.history(period="6d", interval="1d", auto_adjust=False)
        if d.empty or len(d) < 2:
            return None
        prev_close = float(d["Close"].iloc[-2])
        last_close = float(d["Close"].iloc[-1])

        # できれば分足で直近値（プレ・アフターや先物の動き）を拾う
        try:
            m = tk.history(period="2d", interval="1m", auto_adjust=False)
            if not m.empty:
                last = float(m["Close"].iloc[-1])
            else:
                last = last_close
        except Exception:
            last = last_close

        pct_d = (last - prev_close) / prev_close * 100.0 if prev_close else 0.0
        return {"last": last, "pct_d": pct_d, "prev_close": prev_close}
    except Exception:
        return None

def _quote_yield_pct(sym: str) -> Optional[Dict[str, float]]:
    """
    金利用：^TNX は10倍スケールなので補正。その他はそのまま。
    """
    q = _quote_last_and_pctd(sym)
    if not q:
        return None
    last = q["last"]
    # ^TNX は 1=0.1% のスケール
    if sym.upper() == "^TNX":
        last /= 10.0
        prev = q["prev_close"] / 10.0
        pct_d = (last - prev) / prev * 100.0 if prev else 0.0
        return {"last": last, "pct_d": pct_d}
    return {"last": last, "pct_d": q["pct_d"]}


# ------------ 各アセット取得 ------------
def fetch_fx() -> Dict[str, Any]:
    q = _quote_last_and_pctd("USDJPY=X")
    out: Dict[str, Any] = {}
    if q:
        out["USDJPY"] = {"spot": round(q["last"], 3), "pct_d": round(q["pct_d"], 3)}
    return out

def fetch_futures() -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    # 日経225先物（大証／CME mini の順でフォールバック）
    nikkei_sym = _pick_symbol(["NK=F", "NIY=F"]) or "NK=F"
    q = _quote_last_and_pctd(nikkei_sym)
    if q:
        out["NK225"] = {"last": round(q["last"], 2), "pct_d": round(q["pct_d"], 2), "symbol": nikkei_sym}

    # TOPIX は先物シンボルが不安定なため指数をプロキシ
    q = _quote_last_and_pctd("^TOPX")
    if q:
        out["TOPIX"] = {"last": round(q["last"], 2), "pct_d": round(q["pct_d"], 2), "symbol": "^TOPX"}

    # US先物（E-mini）
    for name, sym in [("SPX", "ES=F"), ("NDX", "NQ=F"), ("DJI", "YM=F")]:
        q = _quote_last_and_pctd(sym)
        if q:
            out[name] = {"last": round(q["last"], 2), "pct_d": round(q["pct_d"], 2), "symbol": sym}
    return out

def fetch_vol() -> Dict[str, Any]:
    q = _quote_last_and_pctd("^VIX")
    return {"VIX": {"last": round(q["last"], 2), "pct_d": round(q["pct_d"], 2)}} if q else {}

def fetch_rates() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    # 米10年
    us = _quote_yield_pct("^TNX")
    if us:
        out["US10Y"] = {"last": round(us["last"], 3), "pct_d": round(us["pct_d"], 2)}
    # 日本10年（候補を試す）
    jp_sym = _pick_symbol(["JP10Y", "^JGB10YR"])
    if jp_sym:
        jp = _quote_last_and_pctd(jp_sym)
        if jp:
            out["JP10Y"] = {"last": round(jp["last"], 3), "pct_d": round(jp["pct_d"], 2), "symbol": jp_sym}
    return out

def fetch_cmd() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, sym in [("XAUUSD", "XAUUSD=X"), ("WTI", "CL=F")]:
        q = _quote_last_and_pctd(sym)
        if q:
            out[name] = {"last": round(q["last"], 2), "pct_d": round(q["pct_d"], 2), "symbol": sym}
    return out

def fetch_crypto() -> Dict[str, Any]:
    q = _quote_last_and_pctd("BTC-USD")
    return {"BTCUSD": {"last": round(q["last"], 2), "pct_d": round(q["pct_d"], 2)}} if q else {}


# ------------ コマンド本体 ------------
class Command(BaseCommand):
    help = "実データ（Yahoo Finance）で先物/VIX/為替/金利/コモディティ/仮想通貨のスナップショット＋セクターRSを保存"

    def add_arguments(self, parser):
        parser.add_argument("--tag", type=str, default="", help="任意タグ（将来拡張）")

    def handle(self, *args, **opts):
        now = datetime.now(dt_tz.utc).astimezone()  # local tz

        # --- マーケット・スナップショット（実データ） ---
        payload = {
            "ts": now.isoformat(timespec="seconds"),
            "fx": fetch_fx(),
            "futures": fetch_futures(),
            "vol": fetch_vol(),
            "rates": fetch_rates(),
            "cmd": fetch_cmd(),
            "crypto": fetch_crypto(),
            "source": "yfinance",
        }

        base = os.path.join(_media_root(), "market", "snapshots", now.strftime("%Y-%m-%d"))
        _ensure_dir(base)
        path_ts = os.path.join(base, f"{now.strftime('%H%M')}.json")
        latest = os.path.join(_media_root(), "market", "snapshots", "latest.json")

        _save_json(path_ts, payload)
        _save_json(latest, payload)
        self.stdout.write(self.style.SUCCESS(f"Wrote snapshot: {path_ts}"))

        # --- セクターRSも同じタイミングで保存（5〜10分おき） ---
        if latest_sector_strength is not None:
            try:
                rs_tbl = latest_sector_strength() or {}
                mdir = os.path.join(_media_root(), "market")

                ts2 = now.strftime("%Y-%m-%d_%H%M")
                path_rs_ts = os.path.join(mdir, f"sector_rs_{ts2}.json")
                path_rs_day = os.path.join(mdir, f"sector_rs_{now.strftime('%Y-%m-%d')}.json")
                path_rs_latest = os.path.join(mdir, "sector_rs_latest.json")

                _save_json(path_rs_ts, rs_tbl)     # 5/10分ごと
                _save_json(path_rs_day, rs_tbl)    # 同日スナップショット
                _save_json(path_rs_latest, rs_tbl) # 最新

                self.stdout.write(self.style.SUCCESS(f"Wrote sector RS: {path_rs_ts}"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"sector RS save skipped: {e}"))
        else:
            self.stdout.write(self.style.WARNING("sector RS not saved (latest_sector_strength() unavailable)."))