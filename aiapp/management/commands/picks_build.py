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

# ===== 基本パス =====
PICKS_DIR = pathlib.Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"
UNIVERSE_DIR = pathlib.Path("aiapp/data/universe")

# ===== 挙動を環境変数で調整 =====
ENV = os.getenv

MIN_SCORE = float(ENV("AIAPP_MIN_SCORE", "0.0"))          # スコア下限（liteは基本0でOK）
REQUIRE_TREND = bool(int(ENV("AIAPP_REQUIRE_TREND", "0"))) # トレンド必須フラグ
SKIP_LIQ = bool(int(ENV("AIAPP_SKIP_LIQ", "1")))           # 流動性フィルタ（現状未使用・将来用）
ALLOW_ETF = bool(int(ENV("AIAPP_ALLOW_ETF", "1")))         # ETF許可

MAX_WORKERS = max(1, int(ENV("AIAPP_BUILD_WORKERS", "8")))
MIN_BARS_LITE = max(1, int(ENV("AIAPP_MIN_BARS_LITE", "30")))
MIN_BARS_FULL = max(1, int(ENV("AIAPP_MIN_BARS_FULL", "120")))

# ===== ユーティリティ =====
def _ensure_dir(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _load_universe(name: str, sample: int | None, head: int | None) -> list[tuple[str, str]]:
    """
    ユニバースを (code, name) のリストで返す。
    ファイル: aiapp/data/universe/<name>.txt（コード1行ずつ）
    特別名: all / jp-all / jpall → StockMaster 全件
    """
    if name.lower() in ("all", "jp-all", "jpall"):
        qs = list(StockMaster.objects.values_list("code", "name"))
    else:
        path = UNIVERSE_DIR / f"{name}.txt"
        if not path.exists():
            raise CommandError(f"universe file not found: {path}")
        codes = [c.strip() for c in path.read_text().splitlines() if c.strip()]
        name_map = dict(StockMaster.objects.filter(code__in=codes).values_list("code", "name"))
        qs = [(c, name_map.get(c, c)) for c in codes]

    if head:
        qs = qs[: int(head)]
    if sample and len(qs) > sample:
        qs = qs[: int(sample)]
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
        # symlink禁止の場合はコピー
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
    # ETFは小数1桁、個別株は整数円で丸め
    return round(x, 1) if etf else int(round(x))

def _normalize_scores(items: list[dict]) -> None:
    """
    items[*]['score']（raw）から score_100 と stars を付与（ユニバース内相対評価）
    """
    if not items:
        return
    raw = [it["score"] for it in items]
    lo, hi = min(raw), max(raw)
    rng = max(1e-9, hi - lo)
    for it in items:
        pct = (it["score"] - lo) / rng  # 0..1
        it["score_100"] = int(round(100 * pct))
        it["stars"] = max(1, min(5, int(round(1 + 4 * pct))))  # 1〜5★

def _pick_sector_map(codes: list[str]) -> dict[str, str]:
    q = StockMaster.objects.filter(code__in=codes).values_list("code", "sector_name")
    return {c: (s or "") for c, s in q}

# ===== コア生成 =====
def _build_items(
    codes: list[tuple[str, str]],
    budget_sec: int,
    nbars: int,
    mode: str,
    horizon: str,
    lite_mode: bool,
) -> list[dict]:
    """
    タイムボックス内で並列に銘柄を処理し、成功分のみ返す。
    ATRベースで Entry/TP/SL を算出。最後に相対スコア化。
    """
    start = time.time()
    items: list[dict] = []

    min_bars = MIN_BARS_LITE if lite_mode else MIN_BARS_FULL

    def work(code: str, name: str) -> dict | None:
        # 価格取得
        df = get_prices(code, nbars)
        if df is None or df.empty or len(df) < min_bars:
            return None

        # 列名は lower 前提（fetch_price が lower を返している）
        try:
            close = df["close"].iloc[-1]
            high = df["high"]
            low = df["low"]
        except Exception:
            # 保険：MultiIndex 等の想定外に備える
            cols = [c.lower() if isinstance(c, str) else c for c in df.columns]
            df.columns = cols
            close = df["close"].iloc[-1]
            high = df["high"]
            low = df["low"]

        # 特徴量 & raw スコア
        feat = compute_features(df)
        raw_s = float(score_sample(feat, mode=mode, horizon=horizon))

        # ボラ（ATR）による TP/SL 幅（フォールバックあり）
        atr14 = float((high - low).rolling(14).mean().iloc[-1])
        if math.isnan(atr14) or atr14 <= 0:
            atr14 = float(close) * 0.015  # 1.5% フォールバック

        is_etf = _is_etf(code)

        last = float(close)
        entry = last  # 終値基準（翌寄り・成行の想定に近い）
        tp = last + 1.5 * atr14
        sl = last - 1.0 * atr14

        # 表示丸め
        last_r = _yen_round(last, is_etf)
        entry_r = _yen_round(entry, is_etf)
        tp_r = _yen_round(tp, is_etf)
        sl_r = _yen_round(sl, is_etf)

        qty = 100
        required_cash = int(round(last * qty))
        est_pl = int(round((tp - entry) * qty))
        est_loss = int(round((entry - sl) * qty))

        # フィルタ
        if raw_s < MIN_SCORE:
            return None
        if REQUIRE_TREND:
            # 例：20日変化率が負なら弾く等、必要ならここに条件を書く
            try:
                if float(df["close"].pct_change(20).iloc[-1]) < 0:
                    return None
            except Exception:
                pass

        return {
            "code": code,
            "name": name,
            "name_norm": name,
            "sector": "",  # 後で埋める
            "last_close": last_r,
            "entry": entry_r,
            "tp": tp_r,
            "sl": sl_r,
            "score": float(raw_s),      # ← raw（後段で score_100 / stars を付与）
            "qty": qty,
            "required_cash": required_cash,
            "est_pl": est_pl,
            "est_loss": est_loss,
            # デバッグ用の理由（簡易）
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
                # 個別失敗は握りつぶして続行
                pass

    # セクター付与
    sec_map = _pick_sector_map([x["code"] for x in items])
    for it in items:
        it["sector"] = sec_map.get(it["code"], "")

    # 相対スコア化（星・100点化）
    _normalize_scores(items)

    # 上位10件に丸め（UI想定）
    items = sorted(items, key=lambda x: x["score_100"], reverse=True)[:10]
    return items

# ===== Django コマンド =====
class Command(BaseCommand):
    help = "AIピック生成（LITE/FULL・スナップショット対応）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="all", help="all / jp-all / <file name>")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=90, help="秒")
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument(
            "--nbars-lite", dest="nbars_lite", type=int, default=60, help="ライト時の足本数"
        )
        parser.add_argument("--use-snapshot", dest="use_snapshot", action="store_true",
                            help="夜間スナップショット利用")
        parser.add_argument("--lite-only", action="store_true", help="ライト生成のみ")
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

        pairs = _load_universe(universe, sample, head)
        if not pairs:
            self.stdout.write(self.style.WARNING("[picks_build] universe=0"))
            return

        tag = "short_aggressive"

        if lite:
            self.stdout.write(f"[picks_build] start LITE universe={len(pairs)} budget={budget}s")
            items = _build_items(
                pairs, budget, nbars_lite, mode="aggressive", horizon="short", lite_mode=True
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
        self.stdout.write(f"[picks_build] start FULL universe={len(pairs)} budget={budget}s use_snapshot={use_snap}")
        items = _build_items(
            pairs, budget, nbars, mode="aggressive", horizon="short", lite_mode=False
        )

        p = _json_path(tag)
        p.write_text(json.dumps({
            "items": items,
            "mode": "SNAPSHOT" if use_snap else "FULL",
            "updated_at": dt.datetime.now().isoformat(),
        }, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        _link_latest(p, "latest.json")
        self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")