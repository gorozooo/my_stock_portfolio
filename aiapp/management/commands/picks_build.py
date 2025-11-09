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

# しきい値・振る舞い（環境変数で調整可能）
MIN_SCORE = float(os.getenv("AIAPP_MIN_SCORE", "0.0"))
REQUIRE_TREND = bool(int(os.getenv("AIAPP_REQUIRE_TREND", "0")))
SKIP_LIQ = bool(int(os.getenv("AIAPP_SKIP_LIQ", "1")))
ALLOW_ETF = bool(int(os.getenv("AIAPP_ALLOW_ETF", "1")))

def _ensure_dir(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _load_universe(name: str, sample: int | None, head: int | None) -> list[tuple[str, str]]:
    """
    戻り値: [(code, name), ...]
    """
    if name.lower() in ("all", "jp-all", "jpall"):
        qs = list(StockMaster.objects.values_list("code", "name"))
    else:
        path = UNIVERSE_DIR / f"{name}.txt"
        if not path.exists():
            raise CommandError(f"universe file not found: {path}")
        codes = [c.strip() for c in path.read_text().splitlines() if c.strip()]
        # 名前はDBから引く（なければコードをそのまま表示）
        names = {
            c: (StockMaster.objects.filter(code=c).values_list("name", flat=True).first() or c)
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
        # symlink不可環境では実体コピー
        try:
            dst.write_bytes(src.read_bytes())
        except Exception:
            pass

def _required_bars(nbars: int, lite: bool) -> int:
    """
    必要な最低バー本数を動的決定。
    - LITE: 最低30本。nbarsが大きければ半分（例: 60→30, 90→45）まで緩く。
    - FULL: 最低60本。nbarsが大きければ半分（180→90）まで。
    """
    if lite:
        return max(30, int(nbars * 0.5))
    else:
        return max(60, int(nbars * 0.5))

def _work_one(code: str, name: str, nbars: int, lite: bool, mode: str, horizon: str):
    """
    1銘柄分の特徴量計算とスコアリング。条件を満たせばitem辞書を返す。
    """
    df = get_prices(code, nbars)
    if df is None or df.empty:
        return None

    need = _required_bars(nbars, lite)
    if len(df) < need:
        return None

    feat = compute_features(df)
    s = float(score_sample(feat, mode=mode, horizon=horizon))

    # しきい値
    if s < MIN_SCORE:
        return None

    # オプション: トレンド必須なら直近トレンドをチェック
    if REQUIRE_TREND:
        # 例: 20日リターンが正であることを最低条件にする
        try:
            trend_ok = float(df["close"].pct_change(20).iloc[-1]) > 0
            if not trend_ok:
                return None
        except Exception:
            return None

    # ETF除外オプション
    sector = (
        StockMaster.objects.filter(code=code)
        .values_list("sector_name", flat=True)
        .first() or ""
    )
    if not ALLOW_ETF and ("ETF" in sector or "ETN" in sector):
        return None

    last = float(df["close"].iloc[-1])
    atr = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])

    # ここでは最小限の売買案（小数点はUI側で丸め可）
    item = {
        "code": code,
        "name": name,
        "name_norm": name,
        "sector": sector or "",
        "last_close": last,
        "entry": round(last * 1.001),
        "tp": round(last * 1.03),
        "sl": round(last * 0.97),
        "score": round(s, 3),
        "score_100": max(0, min(100, int(round(50 + s * 10)))),
        "stars": max(1, min(5, int(math.floor(0.5 + (50 + s * 10) / 20)))),
        "qty": 100,
        "required_cash": int(last * 100),
        "est_pl": int(last * 0.03 * 100),
        "est_loss": int(last * 0.03 * 100),
        "reasons": {
            "trend": float((df["close"].pct_change(20).iloc[-1]) * 100),
            "rs": float(
                (df["close"].pct_change(20).iloc[-1] - df["close"].pct_change(20).mean()) * 100
            ),
            "vol_signal": float(
                (df["volume"].iloc[-1] / (df["volume"].rolling(20).mean().iloc[-1] + 1e-9))
            ),
            "atr": float(atr if not math.isnan(atr) else 0.0),
        },
    }
    return item

def _build_items(
    codes: list[tuple[str, str]],
    budget_sec: int,
    nbars: int,
    mode: str,
    horizon: str,
    lite: bool,
):
    """
    タイムボックス内で並行処理しながらアイテムを作る。
    条件に合格した分だけ返す（失敗・不合格はスキップ）。
    """
    start = time.time()
    items: list[dict] = []

    # 並列度はネット事情により get_prices 側で制御されるのでここは固定でもOK
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_work_one, c, n, nbars, lite, mode, horizon): (c, n) for c, n in codes}
        for fut in as_completed(futs, timeout=max(2, budget_sec)):
            # タイムボックス超過で打ち切り
            if time.time() - start > budget_sec:
                break
            try:
                it = fut.result(timeout=5)
                if it:
                    items.append(it)
            except Exception:
                # 個別失敗は握りつぶして続行
                pass

    # スコアで降順→上位10件
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
        parser.add_argument(
            "--nbars-lite",
            dest="nbars_lite",
            type=int,
            default=60,
            help="ライト時の足本数（最低30本あれば通す）",
        )
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
            items = _build_items(
                codes, budget, nbars_lite, mode="aggressive", horizon="short", lite=True
            )

            if not items:
                p = _json_path("latest_lite")
                p.write_text(json.dumps(
                    {"items": [], "mode": "LIVE-FAST", "updated_at": dt.datetime.now().isoformat()},
                    ensure_ascii=False
                ))
                _link_latest(p, "latest_lite.json")
                self.stdout.write(self.style.WARNING("[picks_build] lite: items=0 (empty json emitted)"))
                return

            # セクター名（日本語）を補完
            sec_map = {
                c: s for c, s in StockMaster.objects.filter(
                    code__in=[x["code"] for x in items]
                ).values_list("code", "sector_name")
            }
            for it in items:
                it["sector"] = sec_map.get(it["code"], it.get("sector") or "")

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
        items = _build_items(
            codes, budget, nbars, mode="aggressive", horizon="short", lite=False
        )

        sec_map = {
            c: s for c, s in StockMaster.objects.filter(
                code__in=[x["code"] for x in items]
            ).values_list("code", "sector_name")
        }
        for it in items:
            it["sector"] = sec_map.get(it["code"], it.get("sector") or "")

        p = _json_path(tag)
        p.write_text(json.dumps({
            "items": items,
            "mode": "SNAPSHOT" if use_snap else "FULL",
            "updated_at": dt.datetime.now().isoformat(),
        }, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        _link_latest(p, "latest.json")
        self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")