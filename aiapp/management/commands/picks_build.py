# aiapp/management/commands/picks_build.py
# -*- coding: utf-8 -*-
"""
AIピック生成（FULL/LITE/SNAPSHOT対応の堅牢版 + TopK 厳選出力）
- fetch_price.get_prices を通して必ず整形済みOHLCVを受け取る
- 特徴量作成は models.features.make_features
- スコア/信頼度/Entry-TP-SL は services があれば使用、無ければフォールバック
- すべての計算前に Series/np.ndarray を保証し、「arg must be a list, tuple, 1-d array, or Series」を根絶
- 出力は「全件(JSON)」と「TopK(JSON=UI用)」の二系統
  - 全件: latest_full_all.json（監査/検証用）
  - TopK: latest_full.json（UIが読む/上位K件のみ）

＋ 今回の変更:
  - aiapp.services.sizing_service.compute_position_sizing を呼び出し、
    楽天/松井ごとの数量・必要資金・想定利益/損失（手数料込み）を埋め込む
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

# 銘柄名・業種の補完（StockMaster から）
try:
    from aiapp.models import StockMaster
except Exception:
    StockMaster = None  # 環境により未定義でも動くように

# 任意：外部サービス化されていれば使い、無ければ内蔵フォールバック
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

# 数量・必要資金・損益（楽天/松井）ロジック
try:
    from aiapp.services.sizing_service import compute_position_sizing
except Exception:
    compute_position_sizing = None  # sizing 未実装でも落ちないように


# ---------- 環境・入出力 ----------

PICKS_DIR = Path("media/aiapp/picks")
PICKS_DIR.mkdir(parents=True, exist_ok=True)


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


BUILD_LOG = _env_bool("AIAPP_BUILD_LOG", False)


# ---------- 安全ヘルパ ----------

def _safe_series(x) -> pd.Series:
    """
    入力 x を必ず pd.Series[float] にする。
    - None/NaN → 空Series
    - スカラ/配列/Index 等を Series 化
    """
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
    if isinstance(x, (float, int)) and (x != x):  # NaN判定
        return None
    return x


# ---------- 内蔵フォールバック（servicesが未実装でも動く） ----------

def _fallback_score_sample(feat: pd.DataFrame) -> float:
    """
    0.0〜1.0のサンプルスコア。
    - 傾き、RSI、直近リターン等を標準化→シグモイド→加重和
    - 0.227固定など単調な値にならないようにする
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
        0.30 * sig(rsi) +
        0.25 * sig(mom5) +
        0.20 * sig(mom20) +
        0.15 * sig(sl5) +
        0.10 * sig(sl20)
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
    短期×攻め（暫定本番）：高値掴み緩和
      entry = last + 0.05*ATR
      TP    = entry + 0.80*ATR
      SL    = entry - 0.60*ATR
    """
    if not np.isfinite(last) or not np.isfinite(atr) or atr <= 0:
        return None, None, None
    entry = last + 0.05 * atr
    tp = entry + 0.80 * atr
    sl = entry - 0.60 * atr
    return float(entry), float(tp), float(sl)


# ---------- モデル ----------

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
    score: Optional[float] = None        # 0..1
    score_100: Optional[int] = None      # 0..100
    stars: Optional[int] = None          # 1..5

    # 楽天/松井ごとの数量・資金・損益（今回追加）
    qty_rakuten: Optional[int] = None
    qty_matsui: Optional[int] = None
    required_cash_rakuten: Optional[float] = None
    required_cash_matsui: Optional[float] = None
    est_pl_rakuten: Optional[float] = None
    est_pl_matsui: Optional[float] = None
    est_loss_rakuten: Optional[float] = None
    est_loss_matsui: Optional[float] = None

    # 共通情報
    risk_pct: Optional[float] = None
    lot_size: Optional[int] = None

    reasons_text: Optional[List[str]] = None


def _score_to_0_100(s01: float) -> int:
    if not np.isfinite(s01):
        return 0
    return int(round(max(0.0, min(1.0, s01)) * 100))


# ---------- 1銘柄処理 ----------

def _work_one(code: str, nbars: int) -> Optional[PickItem]:
    """
    単銘柄の計算。必ず try/except で落ちないようにし、途中で None を渡さない。
    ここでは「価格系・スコア・Entry/TP/SL」までを計算し、
    数量・必要資金・損益（楽天/松井）は後段で sizing_service が埋める。
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

        # スコア
        if ext_score_sample:
            s01 = float(ext_score_sample(feat))
        else:
            s01 = _fallback_score_sample(feat)

        score100 = _score_to_0_100(s01)
        stars = int(ext_stars_from_score(s01)) if ext_stars_from_score else _fallback_stars(s01)

        # Entry/TP/SL
        if ext_entry_tp_sl:
            e, t, s = ext_entry_tp_sl(last, atr, mode="aggressive", horizon="short")
        else:
            e, t, s = _fallback_entry_tp_sl(last, atr)

        if BUILD_LOG:
            print(f"[picks_build] {code}: last={last} atr={atr} score={s01} score100={score100}")

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
        return item

    except Exception as e:
        print(f"[picks_build] work error for {code}: {e}")
        return None


# ---------- ユニバース読み ----------

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


# ---------- メタ補完（銘柄名・33業種） ----------

def _enrich_meta(items: List[PickItem]) -> None:
    """StockMaster があれば name / sector_display を付与。"""
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
        # 補完失敗は無視
        pass


# ---------- Django command ----------

class Command(BaseCommand):
    help = "AIピック生成（完全版/ライト・スナップショット対応 + TopK 厳選）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", type=str, default="quick_30", help="all / nk225 / quick_100 / <file name>")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=None, help="秒")
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--nbars-lite", type=int, default=45, help="ライトモード時の足本数")
        parser.add_argument("--use-snapshot", action="store_true")
        parser.add_argument("--lite-only", action="store_true")
        parser.add_argument("--force", action="store_true")
        # 仕様上追加
        parser.add_argument("--style", type=str, default="aggressive")
        parser.add_argument("--horizon", type=str, default="short")
        # TopK 追加（UIは厳選のみを読む）
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
            self._emit([], [], mode="full", style=style, horizon=horizon, universe=universe, topk=topk)
            return

        if BUILD_LOG:
            print(f"[picks_build] start FULL universe={len(codes)}")

        # 単ユーザー前提：最初のユーザーを数量計算の基準にする
        User = get_user_model()
        user = User.objects.order_by("id").first()

        items: List[PickItem] = []
        for code in codes:
            it = _work_one(code, nbars=nbars)
            if it is None:
                continue

            # 数量・必要資金・損益（楽天/松井）を埋める
            if (
                compute_position_sizing is not None and
                user is not None and
                it.last_close is not None and
                it.atr is not None and
                it.entry is not None and
                it.tp is not None and
                it.sl is not None
            ):
                sizing = compute_position_sizing(
                    user=user,
                    code=it.code,
                    last_price=float(it.last_close),
                    atr=float(it.atr),
                    entry=float(it.entry),
                    tp=float(it.tp),
                    sl=float(it.sl),
                )

                it.qty_rakuten = sizing.get("qty_rakuten", 0)
                it.qty_matsui = sizing.get("qty_matsui", 0)
                it.required_cash_rakuten = sizing.get("required_cash_rakuten", 0)
                it.required_cash_matsui = sizing.get("required_cash_matsui", 0)
                it.est_pl_rakuten = sizing.get("est_pl_rakuten", 0)
                it.est_pl_matsui = sizing.get("est_pl_matsui", 0)
                it.est_loss_rakuten = sizing.get("est_loss_rakuten", 0)
                it.est_loss_matsui = sizing.get("est_loss_matsui", 0)
                it.risk_pct = sizing.get("risk_pct")
                it.lot_size = sizing.get("lot_size")
            else:
                # sizing が使えない場合は 0 で埋める
                it.qty_rakuten = 0
                it.qty_matsui = 0
                it.required_cash_rakuten = 0
                it.required_cash_matsui = 0
                it.est_pl_rakuten = 0
                it.est_pl_matsui = 0
                it.est_loss_rakuten = 0
                it.est_loss_matsui = 0
                it.risk_pct = None
                it.lot_size = None

            items.append(it)

        # メタ（銘柄名・業種）補完
        _enrich_meta(items)

        # 並び：score_100 desc → last_close desc（見栄え調整）
        items.sort(
            key=lambda x: (
                x.score_100 if x.score_100 is not None else -1,
                x.last_close if x.last_close is not None else -1,
            ),
            reverse=True,
        )

        # TopK 厳選（UI が読むのはこっち）
        top_items = items[:max(0, topk)]

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
        )

    # ------ 出力 ------
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
    ):
        meta = {
            "mode": mode,
            "style": style,
            "horizon": horizon,
            "universe": universe,
            "total": len(all_items),
            "topk": topk,
        }
        data_all = dict(meta=meta, items=[asdict(x) for x in all_items])
        data_top = dict(meta=meta, items=[asdict(x) for x in top_items])

        PICKS_DIR.mkdir(parents=True, exist_ok=True)

        # 監査/検証用：全件
        out_all_latest = PICKS_DIR / "latest_full_all.json"
        out_all_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full_all.json"
        out_all_latest.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))
        out_all_stamp.write_text(json.dumps(data_all, ensure_ascii=False, separators=(",", ":")))

        # UI用：TopK
        out_top_latest = PICKS_DIR / "latest_full.json"
        out_top_stamp = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full.json"
        out_top_latest.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))
        out_top_stamp.write_text(json.dumps(data_top, ensure_ascii=False, separators=(",", ":")))


def dt_now_stamp() -> str:
    from datetime import datetime, timezone, timedelta
    JST = timezone(timedelta(hours=9))
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")