# -*- coding: utf-8 -*-
"""
AIピック生成コマンド（FULL + TopK + Sizing + 理由テキスト）

========================================
▼ 全体フロー（1銘柄あたり）
========================================
  1. 価格取得（OHLCV）
  2. 特徴量生成（テクニカル指標など）
  3. フィルタリング層（仕手株・流動性・異常値などで土台から除外）
  4. スコアリング / ⭐️算出
     ★本番仕様：⭐️は confidence_service（司令塔）で確定
        - 過去30〜90日の仮想エントリー成績（BehaviorStats: 同モード → 無ければ all/all）
        - 特徴量の安定性
        - Entry/TP/SL距離適正
        - scoring_service は補助輪
  5. Entry / TP / SL の計算
  6. Sizing（数量・必要資金・想定PL/損失・見送り理由）
     ★本番（今回）：pTP をEVに混ぜた ev_true_* を使う
  7. 理由テキスト生成（選定理由×最大5行 + 懸念1行）
  8. バイアス層（セクター波 / 大型・小型バランスの微調整）
  9. ランキング（本番）→ JSON 出力

========================================
▼ ランキング（本番仕様）
========================================
  C: EV_true（pTP混ぜた本命EV）降順 → 採用優先
     - まず楽天の EV_true_rakuten を主キーにする（UI表示の基準）
     - qty_rakuten > 0 を優先（0株は下へ）
     - 次点として ml_rank / score_100 / last_close

  TopK（UI用 latest_full.json）：
     - 原則：EV_true_rakuten > 0 かつ qty_rakuten > 0 のみ採用
     - 0件になった場合のみフォールバック（上位から topk 件）

========================================
▼ 利用サービス / モジュール
========================================
  ・価格取得:
      aiapp.services.fetch_price.get_prices

  ・特徴量生成:
      aiapp.models.features.make_features

  ・スコア:
      aiapp.services.scoring_service.score_sample

  ・⭐️（本番）:
      aiapp.services.confidence_service.compute_confidence_star

  ・ML推論（主役の一部）:
      aiapp.services.ml_infer_service.infer_from_features
        ※ p_win / EV / hold_days_pred / tp_first / probs / ml_rank を返す

  ・Entry / TP / SL:
      aiapp.services.entry_service.compute_entry_tp_sl

  ・数量 / 必要資金 / 想定PL / 想定損失 / 見送り理由 / EV_true:
      aiapp.services.sizing_service.compute_position_sizing

  ・理由5つ + 懸念（日本語テキスト）:
      aiapp.services.reasons.make_reasons

  ・銘柄フィルタ層:
      aiapp.services.picks_filters.FilterContext
      aiapp.services.picks_filters.check_all

  ・セクター波 / 大型・小型バランス調整:
      aiapp.services.picks_bias.apply_all

========================================
▼ 出力ファイル
========================================
  - media/aiapp/picks/latest_full_all.json
  - media/aiapp/picks/latest_full.json
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
from aiapp.services.picks_build.ranking_service import sort_items_inplace, select_topk
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


class Command(BaseCommand):
    help = "AIピック生成（FULL + TopK + Sizing + 理由テキキスト）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", type=str, default="nk225", help="all_jpx / nk225 / nikkei_225 / <file name> など")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=None)
        parser.add_argument("--nbars", type=int, default=260)
        parser.add_argument("--nbars-lite", type=int, default=45)
        parser.add_argument("--use-snapshot", action="store_true")
        parser.add_argument("--lite-only", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--style", type=str, default="aggressive")
        parser.add_argument("--horizon", type=str, default="short")
        parser.add_argument("--topk", type=int, default=int(os.getenv("AIAPP_TOPK", "10")), help="上位何銘柄を latest_full.json に出すか")

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
                    print(f"[picks_build] use MacroRegimeSnapshot date={macro_regime.date} regime={macro_regime.regime_label}")
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] macro regime load error: {ex}")

        User = get_user_model()
        user = User.objects.first()

        items = []
        meta_extra = {}
        filter_stats = {}

        behavior_cache = load_behavior_cache(codes)
        if BUILD_LOG:
            print(f"[picks_build] BehaviorStats cache rows: {len(behavior_cache)}")

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
                    print(f"[picks_build] bias error: {ex}")

        sort_items_inplace(items)
        top_items, topk_mode = select_topk(items, topk)

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

        meta_extra["rank_mode"] = "EV_true_rakuten"
        meta_extra["topk_rule"] = "EV_true_rakuten>0 and qty_rakuten>0"
        meta_extra["topk_mode"] = topk_mode

        emit_json(
            items,
            top_items,
            mode="full",
            style=style,
            horizon=horizon,
            universe=universe,
            topk=topk,
            meta_extra=meta_extra,
        )

        if BUILD_LOG:
            print(f"[picks_build] done stockmaster_total={stockmaster_total} total={len(items)} topk={len(top_items)}")