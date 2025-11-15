# aiapp/management/commands/picks_build.py
# -*- coding: utf-8 -*-
"""
AIピック生成（FULL/LITE/SNAPSHOT対応 + TopK + 数量算出 + 理由付き）
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from aiapp.services.fetch_price import get_prices
from aiapp.models.features import make_features, FeatureConfig

try:
    from aiapp.models import StockMaster
except Exception:
    StockMaster = None  # 環境により未定義でも動くように

# 外部サービス（あれば使用）
try:
    from aiapp.services.scoring_service import (
        score_sample as ext_score_sample,
        stars_from_score as ext_stars_from_score,
    )
except Exception:
    ext_score_sample = None
    ext_stars_from_score = None

try:
    from aiapp.services.entry_service import (
        compute_entry_tp_sl as ext_entry_tp_sl,
    )
except Exception:
    ext_entry_tp_sl = None

# 数量・必要資金・理由サービス
try:
    from aiapp.services.sizing_service import compute_position_sizing
except Exception:
    compute_position_sizing = None  # 互換用


PICKS_DIR = Path("media/aiapp/picks")
PICKS_DIR.mkdir(parents=True, exist_ok=True)


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


BUILD_LOG = _env_bool("AIAPP_BUILD_LOG", False)


def _safe_series(x) -> pd.Series:
    if x is None:
        return pd.Series(dtype="float64")
    if isinstance(x, pd.Series):
        return x.astype("float64")
    if isinstance(x, pd.DataFrame):
        if x.shape[1] >= 1:
            return x.iloc[:, -1].astype("float64")
        return pd.Series(dtype="float64")
    try:
        arr = np.asarray(x, dtype="float64")
        if arr.ndim == 0:
            return pd.Series([float(arr)], dtype="float64")
        return pd.Series(arr, dtype="float64")
    except Exception:
        return pd.Series(dtype="float64")


def _safe_float(x) -> float:
    try:
        if x is None:
            return float("nan")
        if isinstance(x, (pd.Series, pd.DataFrame, pd.Index)):
            if len(x) == 0:
                return float("nan")
            if isinstance(x, pd.DataFrame):
                x = x.iloc[:, -1]
            return float(pd.to_numeric(pd.Series(x).iloc[-1], errors="coerce"))
        return float(x)
    except Exception:
        return float("nan")


def _nan_to_none(x):
    if isinstance(x, (float, int)) and (x != x):  # NaN
        return None
    return x


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
    score: Optional[float] = None       # 0..1
    score_100: Optional[int] = None     # 0..100
    stars: Optional[int] = None         # 1..5

    # 数量・資金（楽天）
    qty_rakuten: Optional[int] = None
    required_cash_rakuten: Optional[float] = None
    est_pl_rakuten: Optional[float] = None
    est_loss_rakuten: Optional[float] = None

    # 数量・資金（松井）
    qty_matsui: Optional[int] = None
    required_cash_matsui: Optional[float] = None
    est_pl_matsui: Optional[float] = None
    est_loss_matsui: Optional[float] = None

    # 理由（カード下にまとめて表示用）
    reasons_text: Optional[List[str]] = None


def _score_to_0_100(s01: float) -> int:
    if not np.isfinite(s01):
        return 0
    return int(round(max(0.0, min(1.0, s01)) * 100))


def _fallback_score_sample(feat: pd.DataFrame) -> float:
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
        m, sd = float(s.mean()), float(s.std(ddof=0))
        if not np.isfinite(sd) or sd == 0:
            return pd.Series(np.zeros(len(s)), index=s.index)
        return (s - m) / sd

    def sig(x):
        try:
            return 1.0 / (1.0 + np.exp(-float(x)))
        except Exception:
            return 0.5

    rsi = _safe_float((nz(f["RSI14"])).iloc[-1]) if "RSI14" in f else float("nan")
    mom5 = _safe_float((nz(f["RET_5"])).iloc[-1]) if "RET_5" in f else float("nan")
    mom20 = _safe_float((nz(f["RET_20"])).iloc[-1]) if "RET_20" in f else float("nan")
    sl5 = _safe_float((nz(f["SLOPE_5"])).iloc[-1]) if "SLOPE_5" in f else float("nan")
    sl20 = _safe_float((nz(f["SLOPE_20"])).iloc[-1]) if "SLOPE_20" in f else float("nan")

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
    if not np.isfinite(last) or not np.isfinite(atr) or atr <= 0:
        return None, None, None
    entry = last + 0.05 * atr
    tp = entry + 0.80 * atr
    sl = entry - 0.60 * atr
    return float(entry), float(tp), float(sl)


def _load_universe(name: str) -> List[str]:
    base = Path("aiapp/data/universe")
    txt = base / (name if name.endswith(".txt") else f"{name}.txt")
    if not txt.exists():
        print(f"[picks_build] universe file not found: {txt}")
        return []
    codes = []
    for line in txt.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        codes.append(line.split(",")[0].strip())
    return codes


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


def dt_now_stamp() -> str:
    from datetime import datetime, timezone, timedelta

    JST = timezone(timedelta(hours=9))
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")


class Command(BaseCommand):
    help = "AIピック生成（完全版/ライト・スナップショット対応 + TopK + 数量/理由）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", type=str, default="quick_30")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=None, help="秒")
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--nbars-lite", type=int, default=45)
        parser.add_argument("--use-snapshot", action="store_true")
        parser.add_argument("--lite-only", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--style", type=str, default="aggressive")
        parser.add_argument("--horizon", type=str, default="short")
        parser.add_argument("--topk", type=int, default=int(os.getenv("AIAPP_TOPK", "10")))

    def handle(self, *args, **opts):
        universe = opts.get("universe") or "quick_30"
        nbars = int(opts.get("nbars") or 180)
        style = (opts.get("style") or "aggressive").lower()
        horizon = (opts.get("horizon") or "short").lower()
        topk = int(opts.get("topk") or 10)

        codes = _load_universe(universe)
        if not codes:
            print("[picks_build] items=0 (empty json emitted)")
            self._emit([], [], mode="full", style=style, horizon=horizon, universe=universe, topk=topk,
                       risk_pct=1.0, lot_size=100)
            return

        if BUILD_LOG:
            print(f"[picks_build] start FULL universe={len(codes)}")

        # AIが参照するユーザー（とりあえず最初のユーザーを使用）
        User = get_user_model()
        user = User.objects.order_by("id").first()

        items: List[PickItem] = []
        last_risk_pct = 1.0
        last_lot_size = 100

        for code in codes:
            try:
                raw = get_prices(code, nbars=nbars, period="3y")
                if raw is None or len(raw) == 0:
                    if BUILD_LOG:
                        print(f"[picks_build] {code}: empty price")
                    continue

                feat = make_features(raw, cfg=FeatureConfig())
                if feat is None or len(feat) == 0:
                    if BUILD_LOG:
                        print(f"[picks_build] {code}: empty features")
                    continue

                close_s = _safe_series(feat.get("Close"))
                atr_s = _safe_series(feat.get("ATR14") if "ATR14" in feat else feat.get("ATR", None))

                last = _safe_float(close_s.iloc[-1] if len(close_s) else np.nan)
                atr = _safe_float(atr_s.iloc[-1] if len(atr_s) else np.nan)

                # スコア
                if ext_score_sample:
                    s01 = float(ext_score_sample(feat))
                else:
                    s01 = _fallback_score_sample(feat)

                score100 = _score_to_0_100(s01)
                stars = int(ext_stars_from_score(s01)) if ext_stars_from_score else _fallback_stars(s01)

                # Entry/TP/SL
                if ext_entry_tp_sl:
                    e, t, s = ext_entry_tp_sl(last, atr, mode=style, horizon=horizon)
                else:
                    e, t, s = _fallback_entry_tp_sl(last, atr)

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
                )

                # 数量・必要資金・理由
                if compute_position_sizing is not None and user is not None:
                    sizing = compute_position_sizing(
                        user=user,
                        code=str(code),
                        last_price=last,
                        atr=atr,
                        entry=e if e is not None else last,
                        tp=t if t is not None else last + atr,
                        sl=s if s is not None else last - atr * 0.6,
                    )
                    item.qty_rakuten = sizing.get("qty_rakuten")
                    item.required_cash_rakuten = sizing.get("required_cash_rakuten")
                    item.est_pl_rakuten = sizing.get("est_pl_rakuten")
                    item.est_loss_rakuten = sizing.get("est_loss_rakuten")

                    item.qty_matsui = sizing.get("qty_matsui")
                    item.required_cash_matsui = sizing.get("required_cash_matsui")
                    item.est_pl_matsui = sizing.get("est_pl_matsui")
                    item.est_loss_matsui = sizing.get("est_loss_matsui")

                    # 理由テキストをまとめる（カードの下に出す用）
                    rs = []
                    for label, key in (("楽天", "reasons_rakuten"), ("松井", "reasons_matsui")):
                        lines = sizing.get(key) or []
                        if lines:
                            rs.append(f"")
                            rs.extend(lines)
                    item.reasons_text = rs

                    last_risk_pct = sizing.get("risk_pct", last_risk_pct)
                    last_lot_size = sizing.get("lot_size", last_lot_size)

                items.append(item)

            except Exception as e:
                print(f"[picks_build] work error for {code}: {e}")
                continue

        _enrich_meta(items)

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
            risk_pct=last_risk_pct,
            lot_size=last_lot_size,
        )

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
        risk_pct: float,
        lot_size: int,
    ):
        meta = {
            "mode": mode,
            "style": style,
            "horizon": horizon,
            "universe": universe,
            "total": len(all_items),
            "topk": topk,
            "risk_pct": float(risk_pct),
            "lot_size": int(lot_size),
        }
        data_all = dict(meta=meta, items=[asdict(x) for x in all_items])
        data_top = dict(meta=meta, items=[asdict(x) for x in top_items])

        PICKS_DIR.mkdir(parents=True, exist_ok=True)

        out_all_latest = PICKS_DIR / "latest_full_all.json"
        out_all_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full_all.json"
        out_all_latest.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))
        out_all_stamp.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))

        out_top_latest = PICKS_DIR / "latest_full.json"
        out_top_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full.json"
        out_top_latest.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))
        out_top_stamp.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))