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

# ── 環境変数（デフォルトは安全寄り）────────────────────────────
MIN_SCORE = float(os.getenv("AIAPP_MIN_SCORE", 0.0))
REQUIRE_TREND = bool(int(os.getenv("AIAPP_REQUIRE_TREND", "0")))
SKIP_LIQ = bool(int(os.getenv("AIAPP_SKIP_LIQ", "1")))
ALLOW_ETF = bool(int(os.getenv("AIAPP_ALLOW_ETF", "1")))
MAX_WORKERS = int(os.getenv("AIAPP_BUILD_WORKERS", "3"))

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
        # DBに無い場合はコードをそのまま名前に
        names = {
            c: (
                StockMaster.objects.filter(code=c).first().name
                if StockMaster.objects.filter(code=c).exists()
                else c
            ) for c in codes
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
        # symlink不可環境向けフォールバック
        try:
            dst.write_bytes(src.read_bytes())
        except Exception:
            pass

def _enough_bars(len_df: int, nbars: int) -> bool:
    """要求本数を nbars の 80%（最低20本）に緩和。"""
    required = max(20, int(nbars * 0.8))
    return len_df >= required

def _build_items(
    codes: list[tuple[str, str]],
    budget_sec: int,
    nbars: int,
    mode: str,
    horizon: str,
):
    """
    タイムボックス内で並行処理しながらアイテムを作る。
    - 足本数判定を nbars の 80%（最低20）に緩和
    - スレッド結果待ちを「残り時間」で待つ（固定5秒タイムアウトを撤廃）
    """
    start = time.time()
    items: list[dict] = []

    def work(code: str, name: str):
        df = get_prices(code, nbars)
        if df is None or df.empty or not _enough_bars(len(df), nbars):
            return None

        feat = compute_features(df)
        s = float(score_sample(feat, mode=mode, horizon=horizon))

        # スカラ抽出は .item() でFutureWarning解消
        last = pd.Series(df["close"].iloc[-1]).iloc[0].item() if hasattr(df["close"].iloc[-1], "item") else float(df["close"].iloc[-1])
        atr_ser = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
        atr = pd.Series(atr_ser).iloc[0].item() if hasattr(atr_ser, "item") else float(atr_ser)

        # 事前フィルタ（必要なら）
        if REQUIRE_TREND:
            trend20_ser = df["close"].pct_change(20).iloc[-1]
            trend20 = pd.Series(trend20_ser).iloc[0].item() if hasattr(trend20_ser, "item") else float(trend20_ser)
            if trend20 <= 0:
                return None
        if s < MIN_SCORE:
            return None

        item = {
            "code": code,
            "name": name,
            "name_norm": name,
            "sector": "",
            "last_close": float(last),
            "entry": round(last * 1.001),
            "tp": round(last * 1.03),
            "sl": round(last * 0.97),
            "score": round(float(s), 3),
            "score_100": max(0, min(100, int(round(50 + s * 10)))),
            "stars": max(1, min(5, int(math.floor(0.5 + (50 + s * 10) / 20)))),
            "qty": 100,
            "required_cash": int(last * 100),
            "est_pl": int(last * 0.03 * 100),
            "est_loss": int(last * 0.03 * 100),
        }
        # 参考指標
        trend20_ser = df["close"].pct_change(20).iloc[-1]
        rs_ser = df["close"].pct_change(20)
        vol_sig_ser = df["volume"].iloc[-1] / (df["volume"].rolling(20).mean().iloc[-1] + 1e-9)

        trend20 = pd.Series(trend20_ser).iloc[0].item() if hasattr(trend20_ser, "item") else float(trend20_ser)
        rs_val = (rs_ser.iloc[-1] - rs_ser.mean())
        rs_val = pd.Series(rs_val).iloc[0].item() if hasattr(rs_val, "item") else float(rs_val)
        vol_sig = pd.Series(vol_sig_ser).iloc[0].item() if hasattr(vol_sig_ser, "item") else float(vol_sig_ser)

        item["reasons"] = {
            "trend": float(trend20 * 100),
            "rs": float(rs_val * 100),
            "vol_signal": float(vol_sig),
            "atr": float(atr if not math.isnan(atr) else 0.0),
        }
        return item

    # 並列度は環境変数に従う（既定3）
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(work, c, n): (c, n) for c, n in codes}
        for fut in as_completed(futs, timeout=max(2, budget_sec)):
            # タイムボックス管理：残り時間で待つ
            elapsed = time.time() - start
            remaining = budget_sec - elapsed
            if remaining <= 0:
                break
            try:
                it = fut.result(timeout=remaining)
                if it:
                    items.append(it)
            except Exception:
                # タイムアウト含め黙殺（ログは不要）
                pass

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
                p.write_text(json.dumps(
                    {"items": [], "mode": "LIVE-FAST", "updated_at": dt.datetime.now().isoformat()},
                    ensure_ascii=False))
                _link_latest(p, "latest_lite.json")
                self.stdout.write(self.style.WARNING("[picks_build] lite: items=0 (empty json emitted)"))
                return

            # セクター名付与
            sec_map = {
                c: s for c, s in StockMaster.objects.filter(
                    code__in=[x["code"] for x in items]
                ).values_list("code", "sector_name")
            }
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

        self.stdout.write(f"[picks_build] start FULL universe={len(codes)} budget={budget}s use_snapshot={use_snap}")
        items = _build_items(codes, budget, nbars, mode="aggressive", horizon="short")

        sec_map = {
            c: s for c, s in StockMaster.objects.filter(
                code__in=[x["code"] for x in items]
            ).values_list("code", "sector_name")
        }
        for it in items:
            it["sector"] = sec_map.get(it["code"], "")

        p = _json_path(tag)
        p.write_text(json.dumps({
            "items": items,
            "mode": "SNAPSHOT" if use_snap else "FULL",
            "updated_at": dt.datetime.now().isoformat(),
        }, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        _link_latest(p, "latest.json")
        self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")