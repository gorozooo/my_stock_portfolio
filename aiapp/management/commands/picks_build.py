# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import time
import math
import pathlib
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample

PICKS_DIR = pathlib.Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"
UNIVERSE_DIR = pathlib.Path("aiapp/data/universe")

MIN_SCORE = float(os.getenv("AIAPP_MIN_SCORE", "0.0"))
REQUIRE_TREND = bool(int(os.getenv("AIAPP_REQUIRE_TREND", "0")))
SKIP_LIQ = bool(int(os.getenv("AIAPP_SKIP_LIQ", "1")))
ALLOW_ETF = bool(int(os.getenv("AIAPP_ALLOW_ETF", "1")))
MAX_WORKERS = max(1, int(os.getenv("AIAPP_BUILD_WORKERS", "8")))

def _ensure_dir(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _load_universe(name: str, sample: int | None, head: int | None) -> list[tuple[str, str]]:
    if name.lower() in ("all", "jp-all", "jpall"):
        qs = list(StockMaster.objects.values_list("code", "name"))
    else:
        path = UNIVERSE_DIR / f"{name}.txt"
        if not path.exists():
            raise CommandError(f"universe file not found: {path}")
        codes = [c.strip() for c in path.read_text().splitlines() if c.strip()]
        names = {
            c: (
                StockMaster.objects.filter(code=c).first().name
                if StockMaster.objects.filter(code=c).exists()
                else c
            )
            for c in codes
        }
        qs = [(c, names.get(c, c)) for c in codes]
    if head:
        qs = qs[: int(head)]
    if sample and len(qs) > sample:
        qs = qs[: sample]
    return qs

def _json_path(tag: str) -> pathlib.Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return PICKS_DIR / f"{ts}_{tag}.json"

def _link_latest(src: pathlib.Path, alias: str):
    dst = PICKS_DIR / alias
    try:
        if dst.exists() or dst.is_symlink():
            dst.unlink()
    except Exception:
        pass
    try:
        dst.symlink_to(src.name)
    except Exception:
        try:
            dst.write_bytes(src.read_bytes())
        except Exception:
            pass

def _is_etf(code: str) -> bool:
    sector = (
        StockMaster.objects.filter(code=code)
        .values_list("sector_name", flat=True)
        .first()
    )
    return (sector == "ETF/ETN")

def _yen_round(x: float, etf: bool) -> int | float:
    return round(x, 1) if etf else int(round(x))

def _normalize_scores(items: list[dict]) -> None:
    """
    score_100 / stars を安定化。
    同点（rng≈0）のときは 52点・⭐️3固定にする。
    """
    if not items:
        return
    raw = [float(it.get("score", 0.0)) for it in items]
    lo, hi = min(raw), max(raw)
    rng = hi - lo
    if rng < 1e-6:
        for it in items:
            it["score_100"] = 52
            it["stars"] = 3
        return
    for it in items:
        pct = (float(it["score"]) - lo) / rng
        it["score_100"] = int(round(100 * pct))
        it["stars"] = max(1, min(5, int(round(1 + 4 * pct))))

def _pick_sector_map(codes: list[str]) -> dict[str, str]:
    q = StockMaster.objects.filter(code__in=codes).values_list("code", "sector_name")
    return {c: (s or "") for c, s in q}

def _build_items(codes: list[tuple[str, str]], budget_sec: int, nbars: int, mode: str, horizon: str):
    start = time.time()
    items: list[dict] = []

    def work(code: str, name: str):
        df = get_prices(code, nbars)  # 価格ソースは env AIAPP_PRICE_SOURCES に依存
        min_required = max(30, nbars // 2)  # LITEは緩め
        if df is None or df.empty or len(df) < min_required:
            return None

        # 列名を小文字に正規化（念のため）
        if not all(c in df.columns for c in ("close", "high", "low")):
            df.columns = [str(c).lower() for c in df.columns]

        price_dt = None
        try:
            # pandas の index が DatetimeIndex を仮定
            price_dt = df.index[-1].to_pydatetime()
        except Exception:
            pass

        # 特徴量→スコア
        feat = compute_features(df)
        raw_s = float(score_sample(feat, mode=mode, horizon=horizon))

        # ATR相当（14日）：欠損時は近似
        high, low = df["high"], df["low"]
        atr14 = float((high - low).rolling(14).mean().iloc[-1])
        if math.isnan(atr14) or atr14 <= 0:
            last_tmp = float(df["close"].iloc[-1])
            atr14 = last_tmp * 0.015

        # 価格一式
        is_etf = _is_etf(code)
        last = float(df["close"].iloc[-1])
        entry = last  # LITEは控えめに同値想定
        tp = last + 1.5 * atr14
        sl = last - 1.0 * atr14

        last_r  = _yen_round(last,  is_etf)
        entry_r = _yen_round(entry, is_etf)
        tp_r    = _yen_round(tp,    is_etf)
        sl_r    = _yen_round(sl,    is_etf)

        # 閾値・トレンド必須（必要なら）
        if raw_s < MIN_SCORE:
            return None
        if REQUIRE_TREND:
            try:
                if float(df["close"].pct_change(20).iloc[-1]) < 0:
                    return None
            except Exception:
                pass

        qty = 100
        required_cash = int(round(last * qty))
        est_pl   = int(round((tp - entry) * qty))
        est_loss = int(round((entry - sl) * qty))

        return {
            "code": code,
            "name": name,
            "name_norm": name,
            "sector": "",
            "last_close": last_r,
            "entry": entry_r,
            "tp": tp_r,
            "sl": sl_r,
            "score": raw_s,  # ←内部用。UIでは出さない想定
            "qty": qty,
            "required_cash": required_cash,
            "est_pl": est_pl,
            "est_loss": est_loss,
            # デバッグ/検証用メタ
            "price_date": price_dt.isoformat() if price_dt else "",
            "price_vendor": os.getenv("AIAPP_PRICE_SOURCES", "yfinance"),
            "reasons": {
                "atr": float(atr14),
                "chg20": float(df["close"].pct_change(20).iloc[-1]) if len(df) >= 21 else 0.0,
                "vol_ratio": float(
                    (df["volume"].iloc[-1] / (df["volume"].rolling(20).mean().iloc[-1] + 1e-9))
                ) if "volume" in df.columns else 1.0,
            },
        }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(work, c, n): (c, n) for c, n in codes}
        for fut in as_completed(futs, timeout=max(2, budget_sec)):
            if time.time() - start > budget_sec:
                break
            try:
                it = fut.result(timeout=5)
                if it:
                    items.append(it)
            except Exception:
                pass

    # 業種付与
    sec_map = _pick_sector_map([x["code"] for x in items])
    for it in items:
        it["sector"] = sec_map.get(it["code"], "")

    # スコアを安定化
    _normalize_scores(items)
    items = sorted(items, key=lambda x: (-x["score_100"], -x["stars"], x["code"]))[:10]
    return items

class Command(BaseCommand):
    help = "AIピック生成（LITE/FULL）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="all", help="all / jp-all / <file>")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=90)
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--nbars-lite", dest="nbars_lite", type=int, default=60)
        parser.add_argument("--use-snapshot", dest="use_snapshot", action="store_true")
        parser.add_argument("--lite-only", action="store_true")
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **opts):
        universe = opts["universe"]
        sample = opts["sample"]
        head = opts["head"]
        budget = int(opts["budget"])
        nbars = int(opts.get("nbars", 180))
        nbars_lite = int(opts.get("nbars_lite", 60))
        use_snap = bool(opts.get("use_snapshot", False))
        lite = bool(opts["lite_only"])

        _ensure_dir(PICKS_DIR)
        pairs = _load_universe(universe, sample, head)
        if not pairs:
            self.stdout.write(self.style.WARNING("[picks_build] universe=0"))
            return

        tag = "short_aggressive"

        if lite:
            self.stdout.write(f"[picks_build] start LITE universe={len(pairs)} budget={budget}s")
            items = _build_items(pairs, budget, nbars_lite, mode="aggressive", horizon="short")
            if not items:
                p = _json_path("latest_lite")
                payload = {"items": [], "mode": "LIVE-FAST", "updated_at": dt.datetime.now().isoformat()}
                p.write_text(json.dumps(payload, ensure_ascii=False))
                _link_latest(p, "latest_lite.json")
                _link_latest(p, "latest.json")
                self.stdout.write(self.style.WARNING("[picks_build] lite: items=0 (empty json emitted)"))
                return

            p = _json_path(f"{tag}_lite")
            p.write_text(json.dumps({
                "items": items,
                "mode": "LIVE-FAST",
                "updated_at": dt.datetime.now().isoformat(),
            }, ensure_ascii=False))
            _link_latest(p, "latest_lite.json")
            _link_latest(p, "latest.json")
            self.stdout.write(f"[picks_build] done (lite) items={len(items)} -> {p}")
            return

        self.stdout.write(f"[picks_build] start FULL universe={len(pairs)} budget={budget}s use_snapshot={use_snap}")
        items = _build_items(pairs, budget, nbars, mode="aggressive", horizon="short")
        p = _json_path(tag)
        p.write_text(json.dumps({
            "items": items,
            "mode": "SNAPSHOT" if use_snap else "FULL",
            "updated_at": dt.datetime.now().isoformat(),
        }, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        _link_latest(p, "latest.json")
        self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")