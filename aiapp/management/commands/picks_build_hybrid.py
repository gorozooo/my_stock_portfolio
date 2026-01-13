# aiapp/management/commands/picks_build_hybrid.py
# -*- coding: utf-8 -*-
"""
B側：AIピック生成（テクニカル×ファンダ×政策）。

- A側（既存 picks_build）は触らない
- 出力ファイルは別名:
    - media/aiapp/picks/latest_full_hybrid_all.json
    - media/aiapp/picks/latest_full_hybrid.json
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from aiapp.services.picks_build.settings import BUILD_LOG, JST, PICKS_DIR, dt_now_stamp
from aiapp.services.picks_build.utils import mode_aggr_from_style, mode_period_from_horizon
from aiapp.services.picks_build.universe_service import load_universe, enrich_meta
from aiapp.services.picks_build.behavior_cache_service import load_behavior_cache
from aiapp.services.picks_build.worker_service import work_one
from aiapp.services.picks_build.hybrid_adjust_service import apply_hybrid_adjust

# optional: bias / macro regime
try:
    from aiapp.services.picks_bias import apply_all as apply_bias_all
except Exception:  # pragma: no cover
    apply_bias_all = None  # type: ignore

try:
    from aiapp.models.macro import MacroRegimeSnapshot
except Exception:  # pragma: no cover
    MacroRegimeSnapshot = None  # type: ignore


class Command(BaseCommand):
    help = "AIピック生成（B側: テクニカル×ファンダ×政策）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", type=str, default="nk225", help="all_jpx / nk225 / nikkei_225 / <file name> など")
        parser.add_argument("--nbars", type=int, default=260)
        parser.add_argument("--style", type=str, default="aggressive")
        parser.add_argument("--horizon", type=str, default="short")
        parser.add_argument("--topk", type=int, default=int(os.getenv("AIAPP_TOPK", "10")), help="上位何銘柄を latest_full_hybrid.json に出すか")

    def handle(self, *args, **opts):
        universe = opts.get("universe") or "nk225"
        nbars = int(opts.get("nbars") or 260)
        style = (opts.get("style") or "aggressive").lower()
        horizon = (opts.get("horizon") or "short").lower()
        topk = int(opts.get("topk") or 10)

        mode_period = mode_period_from_horizon(horizon)
        mode_aggr = mode_aggr_from_style(style)

        codes = load_universe(universe)
        stockmaster_total = len(codes)

        # macro regime
        macro_regime = None
        if MacroRegimeSnapshot is not None:
            try:
                today = datetime.now(JST).date()
                macro_regime = (
                    MacroRegimeSnapshot.objects
                    .filter(date__lte=today)
                    .order_by("-date")
                    .first()
                )
                if BUILD_LOG and macro_regime is not None:
                    print(f"[picks_build_hybrid] use MacroRegimeSnapshot date={macro_regime.date} regime={macro_regime.regime_label}")
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build_hybrid] macro regime load error: {ex}")

        User = get_user_model()
        user = User.objects.first()

        items = []
        meta_extra = {}
        filter_stats = {}

        behavior_cache = load_behavior_cache(codes)
        if BUILD_LOG:
            print(f"[picks_build_hybrid] BehaviorStats cache rows: {len(behavior_cache)}")

        for code in codes:
            res = work_one(
                user,
                code,
                nbars=nbars,
                mode_period=mode_period,
                mode_aggr=mode_aggr,
                behavior_cache=behavior_cache,
                filter_stats=filter_stats,
                regime=macro_regime,
            )
            if res is None:
                continue
            item, sizing_meta = res
            items.append(item)

            if sizing_meta:
                if sizing_meta.get("risk_pct") is not None and "risk_pct" not in meta_extra:
                    meta_extra["risk_pct"] = float(sizing_meta["risk_pct"])
                if sizing_meta.get("lot_size") is not None and "lot_size" not in meta_extra:
                    meta_extra["lot_size"] = int(sizing_meta["lot_size"])

        enrich_meta(items)

        if apply_bias_all is not None and items:
            try:
                apply_bias_all(items)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build_hybrid] bias error: {ex}")

        # ★ B側：ファンダ×政策を合成（ev_true_rakuten_hybrid を作る）
        hybrid_stats = apply_hybrid_adjust(items)

        # ★ B側ランキング：hybrid EV を主キーにする（なければ base）
        def _rank_key_b(x):
            ev = x.ev_true_rakuten_hybrid
            if ev is None:
                ev = x.ev_true_rakuten
            ev_key = float(ev) if ev is not None else -1e18

            qty = int(x.qty_rakuten or 0)
            qty_ok = 1 if qty > 0 else 0

            mr = x.ml_rank
            mr_key = float(mr) if mr is not None else -1e18

            sc = float(x.score_100) if x.score_100 is not None else -1e18
            lc = float(x.last_close) if x.last_close is not None else -1e18

            return (ev_key, qty_ok, mr_key, sc, lc)

        items.sort(key=_rank_key_b, reverse=True)

        # TopK（B側）：hybrid EV > 0 かつ qty > 0 を優先。なければフォールバック。
        top_candidates = []
        for it in items:
            ev = it.ev_true_rakuten_hybrid
            if ev is None:
                ev = it.ev_true_rakuten
            qty = int(it.qty_rakuten or 0)
            if ev is not None and float(ev) > 0 and qty > 0:
                top_candidates.append(it)

        top_items = top_candidates[: max(0, topk)]
        topk_mode = "rule:hybrid_ev>0_and_qty>0"
        if not top_items:
            top_items = items[: max(0, topk)]
            topk_mode = "fallback:sorted_top"

        meta_extra["stockmaster_total"] = stockmaster_total
        meta_extra["filter_stats"] = filter_stats

        if macro_regime is not None:
            d = getattr(macro_regime, "date", None)
            meta_extra["regime_date"] = d.isoformat() if d is not None else None
            meta_extra["regime_label"] = getattr(macro_regime, "regime_label", None)
            meta_extra["regime_summary"] = getattr(macro_regime, "summary", None)

        meta_extra["stars_engine"] = "confidence_service"
        meta_extra["stars_mode_period"] = mode_period
        meta_extra["stars_mode_aggr"] = mode_aggr
        meta_extra["behaviorstats_cache_rows"] = len(behavior_cache)

        meta_extra["ml_engine"] = "lightgbm"
        meta_extra["ml_models_dir"] = "media/aiapp/ml/models/latest"

        meta_extra["rank_mode"] = "EV_true_rakuten_hybrid"
        meta_extra["topk_rule"] = "hybrid_ev>0 and qty_rakuten>0"
        meta_extra["topk_mode"] = topk_mode
        meta_extra["hybrid_stats"] = hybrid_stats
        meta_extra["hybrid_mix"] = {"fund_bonus": "(fund_score-50)*0.04", "policy_bonus": "policy_score*0.20", "clamp": "[-6,+6]"}

        self._emit_hybrid(
            items,
            top_items,
            mode="hybrid",
            style=style,
            horizon=horizon,
            universe=universe,
            topk=topk,
            meta_extra=meta_extra,
        )

        if BUILD_LOG:
            print(f"[picks_build_hybrid] done stockmaster_total={stockmaster_total} total={len(items)} topk={len(top_items)}")

    def _emit_hybrid(
        self,
        all_items,
        top_items,
        *,
        mode,
        style,
        horizon,
        universe,
        topk,
        meta_extra,
    ):
        meta = {
            "mode": mode,
            "style": style,
            "horizon": horizon,
            "universe": universe,
            "total": len(all_items),
            "topk": topk,
        }
        meta.update({k: v for k, v in (meta_extra or {}).items() if v is not None})

        data_all = {"meta": meta, "items": [asdict(x) for x in all_items]}
        data_top = {"meta": meta, "items": [asdict(x) for x in top_items]}

        PICKS_DIR.mkdir(parents=True, exist_ok=True)

        # B側（全件）
        out_all_latest = PICKS_DIR / "latest_full_hybrid_all.json"
        out_all_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_hybrid_all.json"
        out_all_latest.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        out_all_stamp.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        # B側（TopK）
        out_top_latest = PICKS_DIR / "latest_full_hybrid.json"
        out_top_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_hybrid.json"
        out_top_latest.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        out_top_stamp.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")