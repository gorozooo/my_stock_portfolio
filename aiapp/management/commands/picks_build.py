# -*- coding: utf-8 -*-
"""
AIピック生成コマンド（FULL + TopK + Sizing + 理由テキスト）

・価格: aiapp.services.fetch_price.get_prices
・特徴量: aiapp.models.features.make_features
・スコア/星: aiapp.services.scoring_service（無ければフォールバック）
・Entry/TP/SL: aiapp.services.entry_service（無ければフォールバック）
・数量/必要資金/想定PL/損失/見送り理由: aiapp.services.sizing_service.compute_position_sizing
・理由5つ＋懸念: aiapp.services.reasons.make_reasons

出力:
  - media/aiapp/picks/latest_full_all.json  … 全銘柄
  - media/aiapp/picks/latest_full.json      … 上位 TopK（UI はこちらを読む）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from aiapp.services.fetch_price import get_prices
from aiapp.models.features import make_features, FeatureConfig
from aiapp.services.sizing_service import compute_position_sizing

# オプション扱いのサービス群（無くても動くように）
try:
    from aiapp.models import StockMaster
except Exception:  # pragma: no cover
    StockMaster = None  # type: ignore

try:
    from aiapp.services.reasons import make_reasons as make_ai_reasons
except Exception:  # pragma: no cover
    make_ai_reasons = None  # type: ignore

try:
    from aiapp.services.scoring_service import (
        score_sample as ext_score_sample,
        stars_from_score as ext_stars_from_score,
    )
except Exception:  # pragma: no cover
    ext_score_sample = None  # type: ignore
    ext_stars_from_score = None  # type: ignore

try:
    from aiapp.services.entry_service import compute_entry_tp_sl as ext_entry_tp_sl
except Exception:  # pragma: no cover
    ext_entry_tp_sl = None  # type: ignore


# =========================================================
# 共通設定
# =========================================================

PICKS_DIR = Path("media/aiapp/picks")
PICKS_DIR.mkdir(parents=True, exist_ok=True)

JST = dt_timezone(timedelta(hours=9))


def dt_now_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


BUILD_LOG = _env_bool("AIAPP_BUILD_LOG", False)


# =========================================================
# ヘルパ
# =========================================================

def _safe_series(x) -> pd.Series:
    """
    どんな形で来ても 1D pd.Series[float] に正規化する。
    """
    if x is None:
        return pd.Series(dtype="float64")
    if isinstance(x, pd.Series):
        return x.astype("float64")
    if isinstance(x, pd.DataFrame):
        if x.shape[1] == 0:
            return pd.Series(dtype="float64")
        return x.iloc[:, -1].astype("float64")
    try:
        arr = np.asarray(x, dtype="float64")
        if arr.ndim == 0:
            return pd.Series([float(arr)], dtype="float64")
        return pd.Series(arr, dtype="float64")
    except Exception:
        return pd.Series(dtype="float64")


def _safe_float(x) -> float:
    """
    スカラ/Series/DataFrame/Index などから float を1つ取り出す。
    失敗時は NaN。
    """
    try:
        if x is None:
            return float("nan")
        if isinstance(x, (pd.Series, pd.Index)):
            if len(x) == 0:
                return float("nan")
            return float(pd.to_numeric(pd.Series(x).iloc[-1], errors="coerce"))
        if isinstance(x, pd.DataFrame):
            if x.shape[1] == 0 or len(x) == 0:
                return float("nan")
            col = x.columns[-1]
            return float(pd.to_numeric(x[col].iloc[-1], errors="coerce"))
        return float(x)
    except Exception:
        return float("nan")


def _nan_to_none(x):
    if isinstance(x, (float, int)) and x != x:  # NaN
        return None
    return x


# =========================================================
# フォールバック実装（サービスが無い場合）
# =========================================================

def _fallback_score_sample(feat: pd.DataFrame) -> float:
    """
    0.0〜1.0 のスコアに正規化する簡易ロジック（テスト用）。
    """
    if feat is None or len(feat) == 0:
        return 0.0

    f = feat.copy()
    for c in ["RSI14", "RET_5", "RET_20", "SLOPE_5", "SLOPE_20"]:
        if c not in f.columns:
            f[c] = np.nan

    def nz(s: pd.Series) -> pd.Series:
        s = _safe_series(s)
        if s.empty:
            return s
        m = float(s.mean())
        sd = float(s.std(ddof=0))
        if not np.isfinite(sd) or sd == 0:
            return pd.Series(np.zeros(len(s)), index=s.index)
        return (s - m) / sd

    def sig(v: float) -> float:
        try:
            return float(1.0 / (1.0 + np.exp(-float(v))))
        except Exception:
            return 0.5

    rsi = _safe_float(nz(f["RSI14"]).iloc[-1])
    mom5 = _safe_float(nz(f["RET_5"]).iloc[-1])
    mom20 = _safe_float(nz(f["RET_20"]).iloc[-1])
    sl5 = _safe_float(nz(f["SLOPE_5"]).iloc[-1])
    sl20 = _safe_float(nz(f["SLOPE_20"]).iloc[-1])

    comp = (
        0.30 * sig(rsi)
        + 0.25 * sig(mom5)
        + 0.20 * sig(mom20)
        + 0.15 * sig(sl5)
        + 0.10 * sig(sl20)
    )
    return float(max(0.0, min(1.0, comp)))


def _fallback_stars(score01: float) -> int:
    if not np.isfinite(score01):
        return 1
    s = max(0.0, min(1.0, float(score01)))
    if s < 0.2:
        return 1
    if s < 0.4:
        return 2
    if s < 0.6:
        return 3
    if s < 0.8:
        return 4
    return 5


def _fallback_entry_tp_sl(last: float, atr: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    暫定・短期×攻め用の Entry / TP / SL。
    """
    if not np.isfinite(last) or not np.isfinite(atr) or atr <= 0:
        return None, None, None
    entry = last + 0.05 * atr
    tp = entry + 0.80 * atr
    sl = entry - 0.60 * atr
    return float(entry), float(tp), float(sl)


def _score_to_0_100(s01: float) -> int:
    if not np.isfinite(s01):
        return 0
    return int(round(max(0.0, min(1.0, s01)) * 100))


# =========================================================
# 出力アイテム
# =========================================================

@dataclass
class PickItem:
    code: str
    name: Optional[str] = None
    sector_display: Optional[str] = None

    last_close: Optional[float] = None
    atr: Optional[float] = None

    entry: Optional[float] = None
    tp: Optional[float] = None
    sl: Optional[float] = None

    score: Optional[float] = None          # 0..1
    score_100: Optional[int] = None        # 0..100
    stars: Optional[int] = None            # 1..5

    qty_rakuten: Optional[int] = None
    required_cash_rakuten: Optional[float] = None
    est_pl_rakuten: Optional[float] = None
    est_loss_rakuten: Optional[float] = None

    qty_matsui: Optional[int] = None
    required_cash_matsui: Optional[float] = None
    est_pl_matsui: Optional[float] = None
    est_loss_matsui: Optional[float] = None

    # sizing_service 側で組んだ共通メッセージ（両方0株など）
    reasons_text: Optional[List[str]] = None

    # 理由5つ＋懸念（reasons サービス）
    reason_lines: Optional[List[str]] = None
    reason_concern: Optional[str] = None

    # 証券会社別の見送り理由（qty=0 のときだけ使用）
    reason_rakuten: Optional[str] = None
    reason_matsui: Optional[str] = None


# =========================================================
# 1銘柄処理
# =========================================================

def _work_one(user, code: str, nbars: int) -> Optional[Tuple[PickItem, Dict[str, Any]]]:
    """
    単一銘柄について、価格→特徴量→スコア→Entry/TP/SL→Sizing→理由 まで全部まとめて計算。
    sizing_meta には risk_pct / lot_size を入れて返す。
    """
    try:
        raw = get_prices(code, nbars=nbars, period="3y")
        if raw is None or len(raw) == 0:
            if BUILD_LOG:
                print(f"[picks_build] {code}: empty price")
            return None

        feat = make_features(raw, cfg=FeatureConfig())
        if feat is None or len(feat) == 0:
            if BUILD_LOG:
                print(f"[picks_build] {code}: empty features")
            return None

        close_s = _safe_series(feat.get("Close"))
        atr_s = _safe_series(feat.get("ATR14") if "ATR14" in feat else feat.get("ATR", None))

        last = _safe_float(close_s.iloc[-1] if len(close_s) else np.nan)
        atr = _safe_float(atr_s.iloc[-1] if len(atr_s) else np.nan)

        # --- スコア ---
        if ext_score_sample:
            s01 = float(ext_score_sample(feat))
        else:
            s01 = _fallback_score_sample(feat)
        score100 = _score_to_0_100(s01)
        stars = int(ext_stars_from_score(s01)) if ext_stars_from_score else _fallback_stars(s01)

        # --- Entry / TP / SL ---
        if ext_entry_tp_sl:
            e, t, s = ext_entry_tp_sl(last, atr, mode="aggressive", horizon="short")
        else:
            e, t, s = _fallback_entry_tp_sl(last, atr)

        # --- 理由5つ＋懸念（特徴量ベース） ---
        reason_lines: Optional[List[str]] = None
        reason_concern: Optional[str] = None
        if make_ai_reasons is not None:
            try:
                last_feat = feat.iloc[-1].to_dict()
                rs, concern = make_ai_reasons(last_feat)
                if rs:
                    reason_lines = list(rs[:5])
                if concern:
                    reason_concern = str(concern)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] reasons error for {code}: {ex}")

        if BUILD_LOG:
            print(
                f"[picks_build] {code} last={last} atr={atr} "
                f"score01={s01:.3f} score100={score100}"
            )

        item = PickItem(
            code=str(code),
            last_close=_nan_to_none(last),
            atr=_nan_to_none(atr),
            entry=_nan_to_none(e),
            tp=_nan_to_none(t),
            sl=_nan_to_none(s),
            score=_nan_to_none(s01),
            score_100=int(score100),
            stars=int(stars),
            reason_lines=reason_lines,
            reason_concern=reason_concern,
        )

        # --- Sizing（数量・必要資金・想定PL/損失 + 見送り理由） ---
        sizing = compute_position_sizing(
            user=user,
            code=str(code),
            last_price=last,
            atr=atr,
            entry=e,
            tp=t,
            sl=s,
        )

        item.qty_rakuten = sizing.get("qty_rakuten")
        item.required_cash_rakuten = sizing.get("required_cash_rakuten")
        item.est_pl_rakuten = sizing.get("est_pl_rakuten")
        item.est_loss_rakuten = sizing.get("est_loss_rakuten")

        item.qty_matsui = sizing.get("qty_matsui")
        item.required_cash_matsui = sizing.get("required_cash_matsui")
        item.est_pl_matsui = sizing.get("est_pl_matsui")
        item.est_loss_matsui = sizing.get("est_loss_matsui")

        # 共通メッセージ
        reasons_text = sizing.get("reasons_text")
        item.reasons_text = reasons_text if reasons_text else None

        # 証券会社別の見送り理由
        item.reason_rakuten = sizing.get("reason_rakuten_msg") or ""
        item.reason_matsui = sizing.get("reason_matsui_msg") or ""

        sizing_meta = {
            "risk_pct": sizing.get("risk_pct"),
            "lot_size": sizing.get("lot_size"),
        }

        # 🔥 追加：特徴量（最終行）を print
        try:
            print(code, feat.iloc[-1].to_dict())
        except Exception:
            print(code, "feat-print-error")

        return item, sizing_meta

    except Exception as e:
        print(f"[picks_build] work error for {code}: {e}")
        return None


# =========================================================
# ユニバース読み込み
# =========================================================

def _load_universe_from_txt(name: str) -> List[str]:
    base = Path("aiapp/data/universe")
    filename = name
    if not filename.endswith(".txt"):
        filename = f"{filename}.txt"
    txt = base / filename
    if not txt.exists():
        print(f"[picks_build] universe file not found: {txt}")
        return []
    codes: List[str] = []
    for line in txt.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        codes.append(line.split(",")[0].strip())
    return codes


def _load_universe_all_jpx() -> List[str]:
    """
    StockMaster から日本株全銘柄コードを取る ALL-JPX 用。
    """
    if StockMaster is None:
        print("[picks_build] StockMaster not available; ALL-JPX empty")
        return []
    try:
        qs = StockMaster.objects.values_list("code", flat=True).order_by("code")
        codes = [str(c).strip() for c in qs if c]
        print(f"[picks_build] ALL-JPX from StockMaster: {len(codes)} codes")
        return codes
    except Exception as e:
        print(f"[picks_build] ALL-JPX load error: {e}")
        return []


def _load_universe(name: str) -> List[str]:
    """
    ユニバース名 → 銘柄コード一覧。
      all_jpx / all / jpx_all         → StockMaster から全件
      nk225 / nikkei225 / nikkei_225  → data/universe/nk225.txt
      それ以外                          → data/universe/<name>.txt
    """
    key = (name or "").strip().lower()

    if key in ("all_jpx", "all", "jpx_all"):
        codes = _load_universe_all_jpx()
        if codes:
            return codes
        print("[picks_build] ALL-JPX fallback to txt")
        return _load_universe_from_txt("all_jpx")

    if key in ("nk225", "nikkei225", "nikkei_225"):
        return _load_universe_from_txt("nk225")

    return _load_universe_from_txt(key)


# =========================================================
# 銘柄名・業種補完
# =========================================================

def _enrich_meta(items: List[PickItem]) -> None:
    if not items or StockMaster is None:
        return
    codes = [it.code for it in items if it and it.code]
    if not codes:
        return
    try:
        qs = StockMaster.objects.filter(code__in=codes).values("code", "name", "sector_name")
        meta: Dict[str, Tuple[str, str]] = {
            str(r["code"]): (r.get("name") or "", r.get("sector_name") or "")
            for r in qs
        }
        for it in items:
            if it.code in meta:
                nm, sec = meta[it.code]
                if not it.name:
                    it.name = nm or None
                if not it.sector_display:
                    it.sector_display = sec or None
    except Exception:
        pass


# =========================================================
# Django management command
# =========================================================

class Command(BaseCommand):
    help = "AIピック生成（FULL + TopK + Sizing + 理由テキスト）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--universe",
            type=str,
            default="nk225",
            help="all_jpx / nk225 / nikkei_225 / <file name> など",
        )
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=None)
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--nbars-lite", type=int, default=45)
        parser.add_argument("--use-snapshot", action="store_true")
        parser.add_argument("--lite-only", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--style", type=str, default="aggressive")
        parser.add_argument("--horizon", type=str, default="short")
        parser.add_argument(
            "--topk",
            type=int,
            default=int(os.getenv("AIAPP_TOPK", "10")),
            help="上位何銘柄を latest_full.json に出すか",
        )

    def handle(self, *args, **opts):
        universe = opts.get("universe") or "nk225"
        nbars = int(opts.get("nbars") or 180)
        style = (opts.get("style") or "aggressive").lower()
        horizon = (opts.get("horizon") or "short").lower()
        topk = int(opts.get("topk") or 10)

        codes = _load_universe(universe)
        if not codes:
            print("[picks_build] universe empty → 空JSON出力")
            self._emit([], [], mode="full", style=style, horizon=horizon, universe=universe, topk=topk, meta_extra={})
            return

        if BUILD_LOG:
            print(f"[picks_build] start FULL universe={universe} codes={len(codes)}")

        User = get_user_model()
        user = User.objects.first()

        items: List[PickItem] = []
        meta_extra: Dict[str, Any] = {}

        for code in codes:
            res = _work_one(user, code, nbars=nbars)
            if res is None:
                continue
            item, sizing_meta = res
            items.append(item)

            # meta（risk_pct / lot_size）は最初に取得できた値を採用
            if sizing_meta:
                if sizing_meta.get("risk_pct") is not None and "risk_pct" not in meta_extra:
                    meta_extra["risk_pct"] = float(sizing_meta["risk_pct"])
                if sizing_meta.get("lot_size") is not None and "lot_size" not in meta_extra:
                    meta_extra["lot_size"] = int(sizing_meta["lot_size"])

        _enrich_meta(items)

        # 並び: score_100 desc → last_close desc
        items.sort(
            key=lambda x: (
                x.score_100 if x.score_100 is not None else -1,
                x.last_close if x.last_close is not None else -1,
            ),
            reverse=True,
        )

        top_items = items[: max(0, topk)]

        if BUILD_LOG:
            print(f"[picks_build] done total={len(items)} topk={len(top_items)}")

        self._emit(
            items,
            top_items,
            mode="full",
            style=style,
            horizon=horizon,
            universe=universe,
            topk=topk,
            meta_extra=meta_extra,
        )

    # -------------------- 出力 --------------------

    def _emit(
        self,
        all_items: List[PickItem],
        top_items: List[PickItem],
        *,
        mode: str,
        style: str,
        horizon: str,
        universe: str,
        topk: int,
        meta_extra: Dict[str, Any],
    ) -> None:
        meta: Dict[str, Any] = {
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

        # 全件（検証用）
        out_all_latest = PICKS_DIR / "latest_full_all.json"
        out_all_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full_all.json"
        out_all_latest.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))
        out_all_stamp.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))

        # TopK（UI用）
        out_top_latest = PICKS_DIR / "latest_full.json"
        out_top_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full.json"
        out_top_latest.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))
        out_top_stamp.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))