# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import time
import math
import pathlib
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample

PICKS_DIR = pathlib.Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"
UNIVERSE_DIR = pathlib.Path("aiapp/data/universe")

# 環境変数（デフォルトは寛容）
MIN_SCORE = float(os.getenv("AIAPP_MIN_SCORE", "0.0"))
REQUIRE_TREND = bool(int(os.getenv("AIAPP_REQUIRE_TREND", "0")))
SKIP_LIQ = bool(int(os.getenv("AIAPP_SKIP_LIQ", "1")))
ALLOW_ETF = bool(int(os.getenv("AIAPP_ALLOW_ETF", "1")))

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

# === ここから今回の重要変更 ===

_UPPER_MAP = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}

def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """列名を大文字OHLCVに正規化。余計な列は残すが、必須は必ず用意。"""
    if df is None or df.empty:
        return df
    cols = {c: _UPPER_MAP.get(c.lower(), c) for c in df.columns}
    out = df.rename(columns=cols).copy()
    for need in ["Open", "High", "Low", "Close", "Volume"]:
        if need not in out.columns:
            out[need] = np.nan
    return out

def _safe_last(s: pd.Series) -> float | None:
    """末尾のスカラを安全にfloat化。NaN/非有限は None。"""
    if s is None or len(s) == 0:
        return None
    v = s.iloc[-1]
    try:
        v = float(v)
    except Exception:
        return None
    if not np.isfinite(v):
        return None
    return v

def _entry_tp_sl_short_aggressive(last: float | None, atr: float | None) -> tuple[float | None, float | None, float | None]:
    """
    短期×攻め（暫定本番）:
      - ATR が有効: entry = last + 0.05*ATR, TP = entry + 1.0*ATR, SL = last - 1.0*ATR
      - ATR が欠損: entry=last, TP=last*1.02, SL=last*0.985
    """
    if last is None:
        return None, None, None
    if atr is None or not np.isfinite(atr) or atr <= 0:
        entry = last
        tp    = last * 1.02
        sl    = last * 0.985
    else:
        entry = last + 0.05 * atr
        tp    = entry + 1.00 * atr
        sl    = last - 1.00 * atr
    return entry, tp, sl

def _stars_from_score100(score100: int | None) -> int:
    """⭐️は 0–100 を 1〜5に割り当て（固定）。"""
    if score100 is None:
        return 1
    # 0–100 → 1–5（20点刻み）
    return int(max(1, min(5, math.floor(score100 / 20) + 1)))

def _score100_from_score(score: float | None) -> int | None:
    if score is None or not np.isfinite(score):
        return None
    x = int(round(50 + score * 10))  # -∞..∞ を概ね 0..100 へ
    return max(0, min(100, x))

# === ここまで重要変更 ===

def _build_items(codes: list[tuple[str, str]], budget_sec: int, nbars: int,
                 mode: str, horizon: str):
    start = time.time()
    items: list[dict] = []

    def work(code: str, name: str):
        df_raw = get_prices(code, nbars)
        if df_raw is None or df_raw.empty:
            return None

        df = _normalize_ohlcv(df_raw)
        if df is None or df.empty or len(df) < 10:
            return None

        # 特徴量
        feat = compute_features(df)

        last = _safe_last(df.get("Close"))
        atr  = _safe_last(feat.get("ATR14"))

        # スコア（本番：scoring側が有効に動くには有効特徴が必要）
        try:
            s = float(score_sample(feat, mode=mode, horizon=horizon))
        except Exception:
            s = float("nan")

        score100 = _score100_from_score(s)
        stars    = _stars_from_score100(score100) if score100 is not None else 1

        entry, tp, sl = _entry_tp_sl_short_aggressive(last, atr)

        # 最低限の足数/スコア条件
        if len(df) < 30:  # FULLは最低30本欲しい
            return None
        if score100 is not None and score100 < int(MIN_SCORE):
            return None

        # 需給/トレンド等の追加条件（REQUIRE_TRENDなど）はここでかける想定
        # 今は通す（実運用で段階的に強化）

        item = {
            "code": code,
            "name": name,
            "name_norm": name,
            "sector": "",
            "last_close": None if last is None else float(last),
            "entry": None if entry is None else float(entry),
            "tp": None if tp is None else float(tp),
            "sl": None if sl is None else float(sl),
            "score": None if not np.isfinite(s) else float(round(s, 3)),
            "score_100": score100,
            "stars": int(stars),
            "qty": 100,
            "required_cash": int(round((last or 0) * 100)),
            "est_pl": int(round(((tp or last or 0) - (entry or last or 0)) * 100)),
            "est_loss": int(round(((entry or last or 0) - (sl or last or 0)) * 100)),
            "reasons": {
                "trend_20d_pct": float(feat["Close"].pct_change(20).iloc[-1] * 100.0) if len(feat) >= 21 else 0.0,
                "rs_20d": float((feat["Close"].pct_change(20).iloc[-1] - feat["Close"].pct_change(20).mean()) * 100.0) if len(feat) >= 21 else 0.0,
                "vol_ratio": float((feat["Volume"].iloc[-1] / (feat["Volume"].rolling(20).mean().iloc[-1] + 1e-9))) if len(feat) >= 20 else 0.0,
                "atr14": 0.0 if atr is None or not np.isfinite(atr) else float(atr),
            },
        }
        return item

    with ThreadPoolExecutor(max_workers=8) as ex:
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

    # スコアで降順、最大10件
    items = sorted(items, key=lambda x: (x.get("score_100") or -1), reverse=True)[:10]
    return items

class Command(BaseCommand):
    help = "AIピック生成（完全版/ライト・スナップショット対応）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="all", help="all / nk225 / quick_100 / <file name>")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=90, help="秒")
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--nbars-lite", dest="nbars_lite", type=int, default=60, help="ライトモード時の足本数")
        parser.add_argument("--use-snapshot", dest="use_snapshot", action="store_true", help="夜間スナップショット利用")
        parser.add_argument("--lite-only", action="store_true", help="日中ライト表示用")
        parser.add_argument("--style", default="aggressive", help="aggressive/normal/defensive（将来拡張）")
        parser.add_argument("--horizon", default="short", help="short/mid/long（将来拡張）")
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
        style = str(opts.get("style") or "aggressive")
        horizon = str(opts.get("horizon") or "short")
        force = bool(opts["force"])

        _ensure_dir(PICKS_DIR)

        codes = _load_universe(universe, sample, head)
        if not codes:
            self.stdout.write(self.style.WARNING("[picks_build] universe=0"))
            return

        tag = f"{horizon}_{style}"

        if lite:
            self.stdout.write(f"[picks_build] start LITE universe={len(codes)} budget={budget}s")
            items = _build_items(codes, budget, nbars_lite, mode=style, horizon=horizon)
            if not items:
                p = _json_path("latest_lite")
                p.write_text(json.dumps({"items": [], "mode": "LIVE-FAST",
                                         "updated_at": dt.datetime.now().isoformat()},
                                        ensure_ascii=False))
                _link_latest(p, "latest_lite.json")
                self.stdout.write(self.style.WARNING("[picks_build] lite: items=0 (empty json emitted)"))
                return

            sec_map = {
                c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
                .values_list("code", "sector_name")
            }
            for it in items:
                it["sector"] = sec_map.get(it["code"], "") or ""

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

        # FULL
        self.stdout.write(f"[picks_build] start FULL universe={len(codes)} budget={budget}s")
        items = _build_items(codes, budget, nbars, mode=style, horizon=horizon)

        sec_map = {
            c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
            .values_list("code", "sector_name")
        }
        for it in items:
            it["sector"] = sec_map.get(it["code"], "") or ""

        p = _json_path(f"{tag}_full")
        p.write_text(json.dumps({
            "items": items,
            "mode": "FULL" if not use_snap else "SNAPSHOT",
            "updated_at": dt.datetime.now().isoformat(),
        }, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        _link_latest(p, "latest.json")
        if items:
            self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")
        else:
            self.stdout.write(self.style.WARNING("[picks_build] items=0 (empty json emitted)"))