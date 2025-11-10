# -*- coding: utf-8 -*-
from __future__ import annotations

"""
AIピック生成コマンド（FULL/LITE 共通）
- 個別処理のタイムアウトは“無し”。全体の budget でのみ制御。
- pandas 将来変更に備えて scalar 抽出を厳密化（float(Series) 警告を出さない）。
- スコア NaN→0.0 フォールバックで items=0 を回避。
"""

import os
import json
import time
import math
import pathlib
import datetime as dt
from typing import List, Tuple, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample

# -------- パス類 --------
PICKS_DIR = pathlib.Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"
UNIVERSE_DIR = pathlib.Path("aiapp/data/universe")

# -------- 環境変数（開発でも通す寄り） --------
MIN_SCORE = float(os.getenv("AIAPP_MIN_SCORE", "0.0"))
REQUIRE_TREND = bool(int(os.getenv("AIAPP_REQUIRE_TREND", "0")))
SKIP_LIQ = bool(int(os.getenv("AIAPP_SKIP_LIQ", "1")))
ALLOW_ETF = bool(int(os.getenv("AIAPP_ALLOW_ETF", "1")))

# エントリー/TP/SL（短期・攻め 暫定本番）
ENTRY_ATR_K = 0.05
TP_ATR_K = 1.5
SL_ATR_K = 1.0

def _ensure_dir(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _yen(v: float | int | None) -> Optional[int]:
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(round(f))
    except Exception:
        return None

def _load_universe(name: str, sample: Optional[int], head: Optional[int]) -> List[Tuple[str, str]]:
    if name.lower() in ("all", "jp-all", "jpall"):
        qs = list(StockMaster.objects.values_list("code", "name"))
    else:
        path = UNIVERSE_DIR / f"{name}.txt"
        if not path.exists():
            raise CommandError(f"universe file not found: {path}")
        codes = [c.strip() for c in path.read_text().splitlines() if c.strip()]
        name_map: Dict[str, str] = {
            c: (StockMaster.objects.filter(code=c).values_list("name", flat=True).first() or c)
            for c in codes
        }
        qs = [(c, name_map.get(c, c)) for c in codes]

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

def _scalar(x) -> float:
    """
    Seriesやnumpyスカラーを安全にfloatへ。
    pandasの「float(Series)」警告を回避。
    """
    try:
        return float(pd.to_numeric(x).item())
    except Exception:
        try:
            return float(x)
        except Exception:
            return float("nan")

def _calc_atr_like(df: pd.DataFrame, window: int = 14) -> float:
    rng = (df["high"].astype(float) - df["low"].astype(float)).rolling(window, min_periods=1).mean()
    val = _scalar(rng.iloc[-1]) if len(rng) else float("nan")
    if math.isnan(val) or math.isinf(val):
        return 0.0
    return val

def _score_to_100(s: float) -> int:
    raw = 50.0 + 10.0 * float(s)
    return int(max(0, min(100, round(raw))))

def _score_to_stars(s100: int) -> int:
    if s100 >= 85: return 5
    if s100 >= 70: return 4
    if s100 >= 55: return 3
    if s100 >= 40: return 2
    return 1

def _make_item(code: str, name: str, df: pd.DataFrame, mode: str, horizon: str) -> Optional[Dict[str, Any]]:
    if df is None or df.empty or len(df) < 30:
        return None

    # 特徴量→スコア
    feat = compute_features(df)
    s = score_sample(feat, mode=mode, horizon=horizon)
    try:
        s = float(s)
    except Exception:
        s = 0.0
    if np.isnan(s) or np.isinf(s):
        s = 0.0

    if s < MIN_SCORE:
        return None

    # 価格・ATR（安全なスカラー抽出）
    last = _scalar(df["close"].astype(float).iloc[-1])
    atr  = _calc_atr_like(df, window=14)

    entry = _yen(last + ENTRY_ATR_K * atr)
    tp    = _yen((entry if entry is not None else last) + TP_ATR_K * atr)
    sl    = _yen((entry if entry is not None else last) - SL_ATR_K * atr)

    lot = 100
    req_cash = (entry if entry is not None else last) * lot
    est_pl   = (tp - entry) * lot if (tp is not None and entry is not None) else None
    est_loss = (entry - sl) * lot if (sl is not None and entry is not None) else None

    s100  = _score_to_100(s)
    stars = _score_to_stars(s100)

    # 理由（数値）もスカラー抽出で安全化
    trend20 = 0.0
    volr20  = 1.0
    if len(df) >= 21:
        c = df["close"].astype(float)
        trend20 = _scalar(c.pct_change(periods=20, fill_method=None).iloc[-1]) * 100.0
    if len(df) >= 20:
        v = df["volume"].astype(float)
        vr = v / (v.rolling(20, min_periods=1).mean().replace(0, np.nan))
        volr20 = _scalar(vr.fillna(0.0).iloc[-1])

    item = {
        "code": code,
        "name": name,
        "name_norm": name,
        "sector": "",
        "last_close": _yen(last),
        "entry": entry,
        "tp": tp,
        "sl": sl,
        # テンプレ側で “(0.227 …)” を出さない仕様にしているので score はあってもOK
        "score": round(s, 3),
        "score_100": s100,
        "stars": stars,
        "qty": lot,
        "required_cash": _yen(req_cash),
        "est_pl": _yen(est_pl) if est_pl is not None else None,
        "est_loss": _yen(est_loss) if est_loss is not None else None,
        "reasons": {
            "trend_20d_pct": trend20,
            "vol_ratio_20d": volr20,
            "atr_like": float(atr),
        },
    }
    return item

def _build_items(
    codes: List[Tuple[str, str]],
    budget_sec: int,
    nbars: int,
    mode: str,
    horizon: str,
    max_workers: int = 8,
) -> List[Dict[str, Any]]:
    start = time.time()
    items: List[Dict[str, Any]] = []

    def work(code: str, name: str) -> Optional[Dict[str, Any]]:
        df = get_prices(code, nbars)  # 個別タイムアウト無しで“待つ”
        if df is None or df.empty:
            return None
        try:
            return _make_item(code, name, df, mode=mode, horizon=horizon)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_map = {ex.submit(work, c, n): (c, n) for c, n in codes}
        for fut in as_completed(fut_map):
            if time.time() - start > max(0, budget_sec):
                break
            try:
                it = fut.result()  # 個別timeoutを設けない
                if it:
                    items.append(it)
            except Exception:
                pass

    items = sorted(items, key=lambda x: x.get("score_100", 0), reverse=True)[:10]
    return items

class Command(BaseCommand):
    help = "AIピック生成（完全版/ライト・スナップショット対応）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="all", help="all / nk225 / quick_100 / <file name>")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=90, help="秒")
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--nbars-lite", dest="nbars_lite", type=int, default=60,
                            help="ライトモード時の足本数")
        parser.add_argument("--use-snapshot", dest="use_snapshot", action="store_true",
                            help="夜間スナップショット利用")
        parser.add_argument("--lite-only", action="store_true", help="日中ライト表示用")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--style", default="aggressive", choices=["aggressive", "normal", "defensive"])
        parser.add_argument("--horizon", default="short", choices=["short", "mid", "long"])

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

        _ensure_dir(PICKS_DIR)

        codes = _load_universe(universe, sample, head)
        if not codes:
            self.stdout.write(self.style.WARNING("[picks_build] universe=0"))
            p = _json_path("latest_empty")
            p.write_text(json.dumps({"items": [], "mode": "EMPTY",
                                     "updated_at": dt.datetime.now().isoformat()}, ensure_ascii=False))
            _link_latest(p, "latest.json")
            return

        if lite:
            self.stdout.write(f"[picks_build] start LITE universe={len(codes)} budget={budget}s")
            items = _build_items(codes, budget, nbars_lite, mode=style, horizon=horizon)
            if items:
                sec_map: Dict[str, str] = {
                    c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
                    .values_list("code", "sector_name")
                }
                for it in items:
                    it["sector"] = sec_map.get(it["code"], "") or ""

            p = _json_path("short_aggressive_lite")
            payload = {"items": items, "mode": "LIVE-FAST", "updated_at": dt.datetime.now().isoformat()}
            p.write_text(json.dumps(payload, ensure_ascii=False))
            _link_latest(p, "latest_lite.json")
            _link_latest(p, "latest.json")

            if items:
                self.stdout.write(f"[picks_build] done (lite) items={len(items)} -> {p}")
            else:
                self.stdout.write(self.style.WARNING("[picks_build] items=0 (empty json emitted)"))
            return

        # FULL
        self.stdout.write(f"[picks_build] start FULL universe={len(codes)} budget={budget}s")
        items = _build_items(codes, budget, nbars, mode=style, horizon=horizon)
        if items:
            sec_map2: Dict[str, str] = {
                c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
                .values_list("code", "sector_name")
            }
            for it in items:
                it["sector"] = sec_map2.get(it["code"], "") or ""

        p = _json_path("short_aggressive_full")
        payload = {"items": items, "mode": "SNAPSHOT" if use_snap else "FULL",
                   "updated_at": dt.datetime.now().isoformat()}
        p.write_text(json.dumps(payload, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        _link_latest(p, "latest.json")

        if items:
            self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")
        else:
            self.stdout.write(self.style.WARNING("[picks_build] items=0 (empty json emitted)"))