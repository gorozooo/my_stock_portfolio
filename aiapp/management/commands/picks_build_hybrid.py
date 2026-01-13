# aiapp/management/commands/picks_build_hybrid.py
# -*- coding: utf-8 -*-
"""
AIピック生成コマンド（HYBRID = Technical + Fundamental + Policy/Social）

- A(tech)のロジックを壊さずに別出力として運用するためのB版
- fund/policy はスナップショット（JSON）から読み込み
- 出力は schema.PickItem を拡張した同一フォーマットでJSONに落とす
"""

from __future__ import annotations

import os
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from aiapp.services.picks_build.settings import BUILD_LOG, JST
from aiapp.services.picks_build.utils import mode_aggr_from_style, mode_period_from_horizon
from aiapp.services.picks_build.universe_service import load_universe, enrich_meta
from aiapp.services.picks_build.behavior_cache_service import load_behavior_cache
from aiapp.services.picks_build.worker_service import work_one
from aiapp.services.picks_build.emit_service import emit_json

# optional: bias / macro regime
try:
    from aiapp.services.picks_bias import apply_all as apply_bias_all
except Exception:  # pragma: no cover
    apply_bias_all = None  # type: ignore

try:
    from aiapp.models.macro import MacroRegimeSnapshot
except Exception:  # pragma: no cover
    MacroRegimeSnapshot = None  # type: ignore

# fund/policy snapshots
from aiapp.services.fundamentals.repo import load_fund_snapshot
from aiapp.services.policy_news.repo import load_policy_snapshot
from aiapp.services.picks_build_hybrid.hybrid_boost_service import compute_hybrid_boost


class Command(BaseCommand):
    help = "AIピック生成（HYBRID: Technical + Fundamental + Policy/Social）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", type=str, default="nk225", help="all_jpx / nk225 / nikkei_225 / <file name> など")
        parser.add_argument("--nbars", type=int, default=260)
        parser.add_argument("--style", type=str, default="aggressive")
        parser.add_argument("--horizon", type=str, default="short")
        parser.add_argument("--topk", type=int, default=int(os.getenv("AIAPP_TOPK", "10")))

        # snapshot path（任意）
        parser.add_argument("--fund-snapshot", type=str, default=None, help="例: latest_fund.json（省略でlatest）")
        parser.add_argument("--policy-snapshot", type=str, default=None, help="例: latest_policy.json（省略でlatest）")

        # 出力ファイル名を変える（A/B同居）
        parser.add_argument("--out-suffix", type=str, default="hybrid", help="latest_full_<suffix>.json の <suffix>")

    def handle(self, *args, **opts):
        universe = opts.get("universe") or "nk225"
        nbars = int(opts.get("nbars") or 260)
        style = (opts.get("style") or "aggressive").lower()
        horizon = (opts.get("horizon") or "short").lower()
        topk = int(opts.get("topk") or 10)

        fund_path = opts.get("fund_snapshot")
        policy_path = opts.get("policy_snapshot")
        out_suffix = (opts.get("out_suffix") or "hybrid").strip().lower()

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

        # load snapshots（無くても空で動く）
        fund_snap = load_fund_snapshot(fund_path)
        policy_snap = load_policy_snapshot(policy_path)

        behavior_cache = load_behavior_cache(codes)
        if BUILD_LOG:
            print(f"[picks_build_hybrid] BehaviorStats cache rows: {len(behavior_cache)}")
            print(f"[picks_build_hybrid] fund asof={fund_snap.asof} rows={len(fund_snap.rows)}")
            print(f"[picks_build_hybrid] policy asof={policy_snap.asof} sectors={len(policy_snap.sector_rows)}")

        items = []
        meta_extra = {}
        filter_stats = {}

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

            # --- enrich_meta 前でも sector_display が後で入るので、まず fund を code で付与 ---
            fr = fund_snap.rows.get(item.code)
            if fr is not None:
                item.fund_score = float(fr.fund_score)
                item.fund_flags = list(fr.flags or [])

            items.append(item)

            if sizing_meta:
                if sizing_meta.get("risk_pct") is not None and "risk_pct" not in meta_extra:
                    meta_extra["risk_pct"] = float(sizing_meta["risk_pct"])
                if sizing_meta.get("lot_size") is not None and "lot_size" not in meta_extra:
                    meta_extra["lot_size"] = int(sizing_meta["lot_size"])

        # DBから name/sector を補完
        enrich_meta(items)

        # --- policy は sector_display 経由で付与（安定） ---
        for it in items:
            sec = (it.sector_display or "").strip()
            if not sec:
                continue
            pr = policy_snap.sector_rows.get(sec)
            if pr is None:
                continue
            it.policy_score = float(pr.policy_score)
            it.policy_flags = list(pr.flags or [])[:10]

        # --- boost/hybrid_score を計算 ---
        for it in items:
            boost, hscore = compute_hybrid_boost(
                ev_true_rakuten=it.ev_true_rakuten,
                fund_score=it.fund_score,
                policy_score=it.policy_score,
                fund_flags=it.fund_flags,
                policy_flags=it.policy_flags,
            )
            it.hybrid_boost = boost
            it.hybrid_score = hscore

        # bias（任意）
        if apply_bias_all is not None and items:
            try:
                apply_bias_all(items)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build_hybrid] bias error: {ex}")

        # --- ranking: hybrid_score を主キーに（qty優先は維持） ---
        def _rank_key(x):
            h = x.hybrid_score if (x.hybrid_score is not None) else -1e18
            qty = int(x.qty_rakuten or 0)
            qty_ok = 1 if qty > 0 else 0
            mr = x.ml_rank if (x.ml_rank is not None) else -1e18
            sc = float(x.score_100) if x.score_100 is not None else -1e18
            lc = float(x.last_close) if x.last_close is not None else -1e18
            return (float(h), qty_ok, float(mr), float(sc), float(lc))

        items.sort(key=_rank_key, reverse=True)

        # TopK: ルールはAに合わせつつ、hybrid_score で上位を取る
        top_candidates = []
        for it in items:
            qty = int(it.qty_rakuten or 0)
            if qty <= 0:
                continue
            # EV_true>0 を基本にしつつ、hybrid_score が強くてもEVがNone/負なら弾く（暴れ防止）
            if it.ev_true_rakuten is None:
                continue
            if float(it.ev_true_rakuten) <= 0:
                continue
            top_candidates.append(it)

        top_items = top_candidates[: max(0, topk)]
        topk_mode = "rule:ev_true>0_and_qty>0 + rank:hybrid_score"
        if not top_items:
            top_items = items[: max(0, topk)]
            topk_mode = "fallback:sorted_top(rank:hybrid_score)"

        meta_extra["stockmaster_total"] = stockmaster_total
        meta_extra["filter_stats"] = filter_stats

        if macro_regime is not None:
            d = getattr(macro_regime, "date", None)
            meta_extra["regime_date"] = d.isoformat() if d is not None else None
            meta_extra["regime_label"] = getattr(macro_regime, "regime_label", None)
            meta_extra["regime_summary"] = getattr(macro_regime, "summary", None)

        meta_extra["variant"] = "hybrid"
        meta_extra["fund_asof"] = fund_snap.asof
        meta_extra["policy_asof"] = policy_snap.asof
        meta_extra["topk_mode"] = topk_mode

        meta_extra["stars_engine"] = "confidence_service"
        meta_extra["stars_mode_period"] = mode_period
        meta_extra["stars_mode_aggr"] = mode_aggr
        meta_extra["behaviorstats_cache_rows"] = len(behavior_cache)

        meta_extra["rank_mode"] = "HYBRID_SCORE"
        meta_extra["hybrid_rule"] = "hybrid_score = EV_true_rakuten + boost(fund,policy)"

        # 出力：emit_service を使いつつ、ファイル名だけ suffix で分けたいので、環境変数で切替する
        # → 既存 emit_service は固定ファイル名なので、ここで一時的に env を立てて emit_json 側で分岐してもいい
        # ただ今回は確実性優先で emit_json の“hybrid専用版”を別で作るのが安全
        from aiapp.services.picks_build_hybrid.emit_service_hybrid import emit_json_hybrid

        emit_json_hybrid(
            items,
            top_items,
            mode="full",
            style=style,
            horizon=horizon,
            universe=universe,
            topk=topk,
            meta_extra=meta_extra,
            out_suffix=out_suffix,
        )

        if BUILD_LOG:
            print(f"[picks_build_hybrid] done stockmaster_total={stockmaster_total} total={len(items)} topk={len(top_items)}")