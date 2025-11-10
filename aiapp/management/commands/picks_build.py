# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import time
import math
import pathlib
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Dict

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices

# 新サービス層
from aiapp.services.policy_loader import PolicyLoader
from aiapp.services.regime_service import RegimeService
from aiapp.services.scoring_service import ScoringService, _atr
from aiapp.services.confidence_service import ConfidenceService
from aiapp.services.entry_service import EntryService

PICKS_DIR = pathlib.Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"
UNIVERSE_DIR = pathlib.Path("aiapp/data/universe")

# 実行時トグル（環境変数）
MIN_SCORE = float(os.getenv("AIAPP_MIN_SCORE", "0.0"))
REQUIRE_TREND = bool(int(os.getenv("AIAPP_REQUIRE_TREND", "0")))
SKIP_LIQ = bool(int(os.getenv("AIAPP_SKIP_LIQ", "1")))
ALLOW_ETF = bool(int(os.getenv("AIAPP_ALLOW_ETF", "1")))
CONF_PROFILE = os.getenv("AIAPP_CONF_PROFILE", "dev")  # dev/prod

def _ensure_dir(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _load_universe(name: str, sample: int | None, head: int | None) -> List[Tuple[str, str]]:
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
        # symlink不可環境用にコピー
        try:
            dst.write_bytes(src.read_bytes())
        except Exception:
            pass

class Command(BaseCommand):
    help = "AIピック生成（サービス分離版：FULL/LITE共通の計算骨格）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="all", help="all / nk225 / quick_100 / <file name>")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=90, help="秒")
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--nbars-lite", dest="nbars_lite", type=int, default=60,
                            help="ライトモード時の足本数")
        parser.add_argument("--use-snapshot", dest="use_snapshot", action="store_true",
                            help="夜間スナップショット利用（将来）")
        parser.add_argument("--lite-only", action="store_true", help="11:50用の軽量推し出し")
        parser.add_argument("--style", default="aggressive", choices=["aggressive", "normal", "defensive"])
        parser.add_argument("--horizon", default="short", choices=["short", "mid", "long"])
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
        style = str(opts["style"])
        horizon = str(opts["horizon"])
        force = bool(opts["force"])

        _ensure_dir(PICKS_DIR)

        codes = _load_universe(universe, sample, head)
        if not codes:
            self.stdout.write(self.style.WARNING("[picks_build] universe=0"))
            return

        # サービス初期化
        loader = PolicyLoader()
        regime = RegimeService()
        scorer = ScoringService(loader, regime)
        conf   = ConfidenceService(loader)
        entry  = EntryService(loader)

        tag_base = f"{horizon}_{style}"
        tag = f"{tag_base}_{'lite' if lite else 'full'}"

        self.stdout.write(f"[picks_build] start {'LITE' if lite else 'FULL'} "
                          f"universe={len(codes)} budget={budget}s")

        # LITEは最新指標だけ利用、FULLは nbars を広めに
        use_nbars = nbars_lite if lite else nbars

        # 並列でDF取得＋信号作成
        start = time.time()
        raw_items: List[Dict] = []

        def work(code: str, name: str) -> Dict | None:
            df = get_prices(code, use_nbars)
            if df is None or df.empty:
                return None
            # LITEでも最低限の統計は計算
            if len(df) < 30 and not lite:
                return None
            last = float(df["close"].iloc[-1])
            atr14 = _atr(df, 14)
            sig = scorer.compute_signals(df)
            score_internal = scorer.aggregate_score(sig, mode=style)  # 実数スコア
            return {
                "code": code,
                "name": name,
                "last": last,
                "atr14": atr14,
                "signals": sig,
                "score_internal": float(score_internal),
            }

        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(work, c, n): (c, n) for c, n in codes}
            for fut in as_completed(futs, timeout=max(2, budget)):
                if time.time() - start > budget:
                    break
                try:
                    it = fut.result(timeout=5)
                    if it:
                        raw_items.append(it)
                except Exception:
                    pass

        if not raw_items:
            # 空でもJSONは出す
            p = _json_path(f"{tag_base}_{'lite' if lite else 'full'}")
            p.write_text(json.dumps({"items": [], "mode": "LIVE-FAST" if lite else "FULL",
                                     "updated_at": dt.datetime.now().isoformat()},
                                    ensure_ascii=False))
            _link_latest(p, "latest_lite.json" if lite else "latest_full.json")
            if not lite:
                _link_latest(p, "latest.json")
            self.stdout.write(self.style.WARNING("[picks_build] items=0 (empty json emitted)"))
            return

        # Universe相対化 → score_100
        scores = [x["score_internal"] for x in raw_items]
        score100_list = scorer.to_percentile(scores)
        for x, s100 in zip(raw_items, score100_list):
            x["score_100"] = int(s100)

        # フィルタ（MIN_SCORE/トレンド要件など）
        selected = []
        for x in raw_items:
            if REQUIRE_TREND and x["signals"].get("trend20", 0.0) <= 0:
                continue
            # MIN_SCOREはscore_100ベース
            if x["score_100"] < MIN_SCORE:
                continue
            selected.append(x)

        # 信頼度（⭐）
        stars_list = conf.batch_assign([it["score_100"] for it in selected])
        for it, st in zip(selected, stars_list):
            it["stars"] = int(st)

        # セクター表記
        sec_map = {
            c: s for c, s in StockMaster.objects
            .filter(code__in=[x["code"] for x in selected])
            .values_list("code", "sector_name")
        }

        # Entry/TP/SL・数量・想定PL等のUI項目へ変換
        items_out: List[Dict] = []
        for it in selected:
            last = it["last"]
            atr14 = it["atr14"]
            ets = entry.propose(last, atr14, mode=style)

            qty = 100  # TODO: lotやリスク設定に連動（将来）
            required_cash = int(round(last * qty))
            est_pl = int(round(max(0.0, (ets["tp"] - last) * qty)))
            est_loss = int(round(max(0.0, (last - ets["sl"]) * qty)))

            items_out.append({
                "code": it["code"],
                "name": it["name"],
                "name_norm": it["name"],
                "sector": sec_map.get(it["code"], "") or "",
                "sector_display": sec_map.get(it["code"], "") or "",
                "last_close": int(round(last)),
                "entry": ets["entry"],
                "tp": ets["tp"],
                "sl": ets["sl"],
                # UIでは数値の生scoreは出さない → None
                "score": None,
                "score_100": int(it["score_100"]),
                "stars": int(it["stars"]),
                "qty": int(qty),
                "required_cash": int(required_cash),
                "est_pl": int(est_pl),
                "est_loss": int(est_loss),
                "reasons_text": [
                    f"20日トレンド: {round(100*it['signals'].get('trend20',0.0),2)}%",
                    f"相対強度(RS20): {round(100*it['signals'].get('rs20',0.0),2)}%",
                    f"短期モメンタム(5日): {round(100*it['signals'].get('mom5',0.0),2)}%",
                    f"出来高比(20日): {round(it['signals'].get('volr',0.0),2)}x",
                ],
            })

        # スコア降順で10件
        items_out = sorted(items_out, key=lambda x: x["score_100"], reverse=True)[:10]

        # 出力
        p = _json_path(f"{tag_base}_{'lite' if lite else 'full'}")
        p.write_text(json.dumps({
            "items": items_out,
            "mode": "LIVE-FAST" if lite else "FULL",
            "updated_at": dt.datetime.now().isoformat(),
        }, ensure_ascii=False))
        if lite:
            _link_latest(p, "latest_lite.json")
        else:
            _link_latest(p, "latest_full.json")
            _link_latest(p, "latest.json")
        self.stdout.write(f"[picks_build] done ({'lite' if lite else 'full'}) items={len(items_out)} -> {p}")