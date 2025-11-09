# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import time
import math
import pathlib
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample

PICKS_DIR = pathlib.Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"
UNIVERSE_DIR = pathlib.Path("aiapp/data/universe")

# 環境変数（既存の意味を維持）
MIN_SCORE = float(os.getenv("AIAPP_MIN_SCORE", 0.0))
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

def _rank01(values: List[float]) -> List[float]:
    """0..1 のパーセンタイルに正規化（同値は同率）"""
    arr = pd.Series(values, dtype="float64")
    if arr.count() == 0:
        return [0.0 for _ in values]
    ranks = arr.rank(pct=True, method="average")  # 0..1
    return ranks.fillna(0.0).tolist()

def _is_etf_like(code: str, sector_display: str | None) -> bool:
    if sector_display == "ETF/ETN":
        return True
    # コード先頭13xx はETFが多いので簡易判定（最終的にはsectorで上書き）
    return code.startswith("13")

def _round_price(x: float, is_etf: bool) -> float:
    if pd.isna(x):
        return x
    return round(float(x), 1) if is_etf else round(float(x))

def _build_items(codes: list[tuple[str, str]], budget_sec: int, nbars: int, mode: str, horizon: str):
    """
    1周目: 価格取得・特徴量抽出（並列）
    2周目: パーセンタイル正規化 → 合成スコア/⭐️ 算出、ATRベースで Entry/TP/SL を決定
    """
    start = time.time()
    raws: List[Dict] = []

    def work(code: str, name: str):
        df = get_prices(code, nbars)
        if df is None or df.empty or len(df) < 45:
            return None
        # 特徴量
        feat = compute_features(df)
        s_raw = float(score_sample(feat, mode=mode, horizon=horizon))  # 参考値（UIでは非表示）
        last = float(df["close"].iloc[-1])
        atr = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])
        mom20 = float(df["close"].pct_change(20).iloc[-1])
        rs20 = float(mom20 - df["close"].pct_change(20).rolling(60).mean().iloc[-1])
        vol_signal = float(
            df["volume"].iloc[-1] / (df["volume"].rolling(20).mean().iloc[-1] + 1e-9)
        )
        price_date = str(getattr(df.index, "date", lambda: df.index[-1])()[-1]) if hasattr(df.index, "__iter__") else ""

        return {
            "code": code,
            "name": name,
            "name_norm": name,
            "sector": "",  # 後で付与
            "last_close": last,
            "atr": atr if not math.isnan(atr) else 0.0,
            "mom20": mom20 if not math.isnan(mom20) else 0.0,
            "rs20": rs20 if not math.isnan(rs20) else 0.0,
            "vol_signal": vol_signal if not math.isnan(vol_signal) else 0.0,
            "s_raw": s_raw,
            "price_date": str(df.index[-1].date()) if len(df.index) else "",
            "price_vendor": "yfinance",
        }

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(work, c, n): (c, n) for c, n in codes}
        for fut in as_completed(futs, timeout=max(2, budget_sec)):
            if time.time() - start > budget_sec:
                break
            try:
                res = fut.result(timeout=5)
                if res:
                    raws.append(res)
            except Exception:
                pass

    if not raws:
        return []

    # 2周目: 相対スコア化（0..1）
    r_mom = _rank01([r["mom20"] for r in raws])
    r_rs = _rank01([r["rs20"] for r in raws])
    r_vol = _rank01([r["vol_signal"] for r in raws])

    # sector 名を引いて ETF 判定と丸めに使う
    sec_map: Dict[str, str] = {
        c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in raws])
        .values_list("code", "sector_name")
    }

    items: List[Dict] = []
    for i, r in enumerate(raws):
        code = r["code"]
        sec_name = sec_map.get(code, "") or ""
        etf_flag = _is_etf_like(code, "ETF/ETN" if sec_name == "ETF/ETN" else None)

        # 合成スコア（0..1）: モメ 60%、RS 25%、出来高 15%
        score01 = 0.60 * r_mom[i] + 0.25 * r_rs[i] + 0.15 * r_vol[i]
        score_100 = int(round(score01 * 100))

        # ⭐️: 0..1 を 5段階に
        if score01 < 0.20:
            stars = 1
        elif score01 < 0.40:
            stars = 2
        elif score01 < 0.60:
            stars = 3
        elif score01 < 0.80:
            stars = 4
        else:
            stars = 5

        last = r["last_close"]
        atr = r["atr"]
        step = 0.1 if etf_flag else 1.0

        entry = last + 0.10 * atr
        tp = entry + 0.80 * atr
        sl = entry - 0.60 * atr

        item = {
            "code": code,
            "name": r["name"],
            "name_norm": r["name"],
            "sector": sec_name,
            "last_close": _round_price(last, etf_flag),
            "entry": _round_price(entry, etf_flag),
            "tp": _round_price(tp, etf_flag),
            "sl": _round_price(sl, etf_flag),
            "score": None,                # 内部の 0.227… は UI で非表示にするため None
            "score_100": score_100,
            "stars": stars,
            "qty": 100,
            "required_cash": int(round(last * 100)),
            "est_pl": int(round((tp - entry) * 100)),
            "est_loss": int(round((entry - sl) * 100)),
            "reasons": {
                "trend": float(r["mom20"] * 100.0),
                "rs": float(r["rs20"] * 100.0),
                "vol_signal": float(r["vol_signal"]),
                "atr": float(atr),
            },
            "price_date": r["price_date"],
            "price_vendor": r["price_vendor"],
        }
        items.append(item)

    # 並べ替え（上位10件）
    items = sorted(items, key=lambda x: x["score_100"], reverse=True)[:10]
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

    def handle(self, *args, **opts):
        universe = opts["universe"]
        sample = opts["sample"]
        head = opts["head"]
        budget = int(opts["budget"])
        nbars = int(opts.get("nbars", 180))
        nbars_lite = int(opts.get("nbars_lite", 60))
        use_snap = bool(opts.get("use_snapshot", False))
        lite = bool(opts["lite_only"])
        force = bool(opts["force"])

        _ensure_dir(PICKS_DIR)

        codes = _load_universe(universe, sample, head)
        if not codes:
            self.stdout.write(self.style.WARNING("[picks_build] universe=0"))
            return

        tag = "short_aggressive"

        if lite:
            self.stdout.write(f"[picks_build] start LITE universe={len(codes)} budget={budget}s")
            items = _build_items(codes, budget, nbars_lite, mode="aggressive", horizon="short")
            if not items:
                p = _json_path("latest_lite")
                p.write_text(json.dumps({"items": [], "mode": "LIVE-FAST",
                                         "updated_at": dt.datetime.now().isoformat()},
                                        ensure_ascii=False))
                _link_latest(p, "latest_lite.json")
                self.stdout.write(self.style.WARNING("[picks_build] lite: items=0 (empty json emitted)"))
                return

            # sector 表示のための置換
            sec_disp = {
                c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
                .values_list("code", "sector_name")
            }
            for it in items:
                it["sector"] = sec_disp.get(it["code"], it.get("sector", ""))

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
        self.stdout.write(f"[picks_build] start FULL universe={len(codes)} budget={budget}s use_snapshot={use_snap}")
        items = _build_items(codes, budget, nbars, mode="aggressive", horizon="short")

        sec_disp = {
            c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
            .values_list("code", "sector_name")
        }
        for it in items:
            it["sector"] = sec_disp.get(it["code"], it.get("sector", ""))

        p = _json_path(tag)
        p.write_text(json.dumps({
            "items": items,
            "mode": "SNAPSHOT" if use_snap else "FULL",
            "updated_at": dt.datetime.now().isoformat(),
        }, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        _link_latest(p, "latest.json")
        self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")