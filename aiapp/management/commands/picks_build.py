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
        names = {c: (StockMaster.objects.filter(code=c).first().name if StockMaster.objects.filter(code=c).exists() else c) for c in codes}
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
        # symlink禁止環境向けに実体コピー
        try:
            dst.write_bytes(src.read_bytes())
        except Exception:
            pass

def _build_items(codes: list[tuple[str, str]], budget_sec: int, nbars: int, mode: str, horizon: str):
    """
    タイムボックス内で並行処理しながらアイテムを作る。
    成功した分だけ返す（失敗はスキップ）。重い処理は compute_features → score_sample。
    """
    start = time.time()
    items = []

    def work(code: str, name: str):
        df = get_prices(code, nbars)
        if df is None or df.empty or len(df) < 45:
            return None
        feat = compute_features(df)
        s = float(score_sample(feat, mode=mode, horizon=horizon))
        last = float(df["close"].iloc[-1])
        atr = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])
        # とりあえず親しみトーンの文章はビュー側で整形。ここでは数値のみ。
        item = {
            "code": code,
            "name": name,
            "name_norm": name,
            "sector": "",  # view 側で StockMaster を引いて埋める
            "last_close": last,
            "entry": round(last * 1.001, 1),
            "tp": round(last * 1.03, 1),
            "sl": round(last * 0.97, 1),
            "score": round(s, 3),
            "score_100": max(0, min(100, int(round(50 + s * 10)))),
            "stars": max(1, min(5, int(math.floor(0.5 + (50 + s * 10) / 20)))),
            "qty": 100,
            "required_cash": int(last * 100),
            "est_pl": int(last * 0.03 * 100),
            "est_loss": int(last * 0.03 * 100),
            "reasons": {
                "trend": float((df["close"].pct_change(20).iloc[-1]) * 100),
                "rs": float((df["close"].pct_change(20).iloc[-1] - df["close"].pct_change(20).mean()) * 100),
                "vol_signal": float((df["volume"].iloc[-1] / (df["volume"].rolling(20).mean().iloc[-1] + 1e-9))),
                "atr": float(atr if not math.isnan(atr) else 0.0),
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

    # スコア降順で上位10
    items = sorted(items, key=lambda x: x["score"], reverse=True)[:10]
    return items


class Command(BaseCommand):
    help = "AIピック生成（完全版/ライト・スナップショット対応）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="all", help="all / nk225 / quick_100 / <file name>")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=90, help="秒")
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--use-snapshot", action="store_true", help="(夜間) スナップショット前提で重い処理OK")
        parser.add_argument("--lite-only", action="store_true", help="(日中) 表示用だけサクッと")
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **opts):
        universe = opts["universe"]
        sample   = opts["sample"]
        head     = opts["head"]
        budget   = int(opts["budget"])
        nbars    = int(opts["nbars"])
        use_snap = bool(opts["use-snapshot"])
        lite     = bool(opts["lite-only"])
        force    = bool(opts["force"])

        _ensure_dir(PICKS_DIR)

        codes = _load_universe(universe, sample, head)
        if not codes:
            self.stdout.write(self.style.WARNING("[picks_build] universe=0"))
            return

        tag = "short_aggressive"
        if lite:
            # ライト版は“表示即応”が目的。時間内で取れた分だけ採用。
            self.stdout.write(f"[picks_build] start LITE universe={len(codes)} budget={budget}s")
            items = _build_items(codes, budget, nbars, mode="aggressive", horizon="short")
            if not items:
                # 何も取れなかったら synthetic は作らず、空をリンク（UIに注意帯を出させる）
                p = _json_path("latest_lite")
                p.write_text(json.dumps({"items": [], "mode": "LIVE-FAST", "updated_at": dt.datetime.now().isoformat()}, ensure_ascii=False))
                _link_latest(p, "latest_lite.json")
                self.stdout.write(self.style.WARNING("[picks_build] lite: items=0 (empty json emitted)"))
                return

            # 33業種名を埋める
            sec_map = {c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items]).values_list("code", "sector_name")}
            for it in items:
                it["sector"] = sec_map.get(it["code"], "")

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

        # 完全版（夜間）: 時間長め・重い処理OK
        self.stdout.write(f"[picks_build] start FULL universe={len(codes)} budget={budget}s use_snapshot={use_snap}")
        # 予算を広めに使ってしっかり集める想定
        items = _build_items(codes, budget, nbars, mode="aggressive", horizon="short")
        # 埋め
        sec_map = {c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items]).values_list("code", "sector_name")}
        for it in items:
            it["sector"] = sec_map.get(it["code"], "")

        # 保存
        p = _json_path(tag)
        p.write_text(json.dumps({
            "items": items,
            "mode": "SNAPSHOT" if use_snap else "FULL",
            "updated_at": dt.datetime.now().isoformat(),
        }, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        # 画面優先は“ある方”へ
        _link_latest(p, "latest.json")
        self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")