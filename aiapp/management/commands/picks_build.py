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
  5. Entry / TP / SL の計算
  6. Sizing（数量・必要資金・想定PL/損失・見送り理由）
  7. 理由テキスト生成（選定理由×最大5行 + 懸念1行）
  8. バイアス層（セクター波 / 大型・小型バランスの微調整）
  9. ランキング（score_100 降順 → 株価降順）→ JSON 出力

========================================
▼ 利用サービス / モジュール
========================================
  ・価格取得:
      aiapp.services.fetch_price.get_prices

  ・特徴量生成:
      aiapp.models.features.make_features
    （OHLCV から MA, ボリンジャー, RSI, MACD, ATR, VWAP,
      RET_x, SLOPE_x, GCROSS/DCROSS などを計算）

  ・スコア / ⭐️:
      aiapp.services.scoring_service.score_sample
      aiapp.services.scoring_service.stars_from_score
    ※ モジュールが無い場合は、picks_build 内のフォールバックで算出。

  ・Entry / TP / SL:
      aiapp.services.entry_service.compute_entry_tp_sl
    ※ 無い場合は ATR ベースのフォールバックを使用。

  ・数量 / 必要資金 / 想定PL / 想定損失 / 見送り理由:
      aiapp.services.sizing_service.compute_position_sizing

  ・理由5つ + 懸念（日本語テキスト）:
      aiapp.services.reasons.make_reasons
    （内部では picks_build 側の _build_reasons_features で
      ema_slope / rel_strength_10 / rsi14 / vol_ma20_ratio /
      breakout_flag / atr14 / vwap_proximity / last_price を渡す）

  ・銘柄フィルタ層（どの銘柄を土台から落とすか）:
      aiapp.services.picks_filters.FilterContext
      aiapp.services.picks_filters.check_all
    （仕手株っぽい銘柄 / 出来高極端 / 価格・ATR異常 などを除外）

  ・セクター波 / 大型・小型バランス調整:
      aiapp.services.picks_bias.apply_all
    （PickItem の score を軽く上下させて、全体の並びをチューニング）

========================================
▼ 出力ファイル
========================================
  - media/aiapp/picks/latest_full_all.json
      → 全評価銘柄（検証・バックテスト・ログ用途）

  - media/aiapp/picks/latest_full.json
      → 上位 TopK 銘柄のみ（UI / LINE などが読むメインのJSON）

※ この management command（picks_build）は、
   上記サービス群をオーケストレーションして JSON を生成する役割に徹し、
   個々の「評価ロジック」は scoring_service / reasons / filters / bias
   などの専用モジュール側で管理する。
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

# 追加: フィルタ層 & バイアス層
try:
    from aiapp.services.picks_filters import FilterContext, check_all as picks_check_all
except Exception:  # pragma: no cover
    FilterContext = None  # type: ignore
    picks_check_all = None  # type: ignore

try:
    from aiapp.services.picks_bias import apply_all as apply_bias_all
except Exception:  # pragma: no cover
    apply_bias_all = None  # type: ignore


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


def _build_reasons_features(feat: pd.DataFrame, last: float, atr: float) -> Dict[str, Any]:
    """
    reasons.make_reasons 用に、features DataFrame から必要な指標だけ抜き出して
    名前を合わせた dict を組み立てる。
    """
    if feat is None or len(feat) == 0:
        return {}

    row = feat.iloc[-1]

    def g(key: str) -> Optional[float]:
        try:
            v = row.get(key)
        except Exception:
            v = None
        if v is None:
            return None
        try:
            f = float(v)
        except Exception:
            return None
        if not np.isfinite(f):
            return None
        return f

    ema_slope = g("SLOPE_20")
    # 相対強度は「20日リターン」を簡易的に％換算して使う
    rel_strength_10 = None
    r20 = g("RET_20")
    if r20 is not None:
        rel_strength_10 = r20 * 100.0  # 例: 0.12 → 12%

    rsi14 = g("RSI14")

    vol = g("Volume")
    ma20 = g("MA20")
    vol_ma20_ratio = None
    if vol is not None and ma20 is not None and ma20 > 0:
        # 仕様上は「出来高 / 20日平均出来高」を想定しているが、
        # 現状 MA20 は価格ベースなので「目安」として扱う。
        vol_ma20_ratio = vol / ma20

    breakout_flag = 0
    gcross = g("GCROSS")
    if gcross is not None and gcross > 0:
        breakout_flag = 1

    vwap_proximity = g("VWAP_GAP_PCT")

    last_price = None
    if np.isfinite(last):
        last_price = float(last)

    atr14 = None
    if np.isfinite(atr):
        atr14 = float(atr)

    return {
        "ema_slope": ema_slope,
        "rel_strength_10": rel_strength_10,
        "rsi14": rsi14,
        "vol_ma20_ratio": vol_ma20_ratio,
        "breakout_flag": breakout_flag,
        "atr14": atr14,
        "vwap_proximity": vwap_proximity,
        "last_price": last_price,
    }


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
  
    qty_sbi: Optional[int] = None
    required_cash_sbi: Optional[float] = None
    est_pl_sbi: Optional[float] = None
    est_loss_sbi: Optional[float] = None
    
    # sizing_service 側で組んだ共通メッセージ（両方0株など）
    reasons_text: Optional[List[str]] = None

    # 理由5つ＋懸念（reasons サービス）
    reason_lines: Optional[List[str]] = None
    reason_concern: Optional[str] = None

    # 証券会社別の見送り理由（qty=0 のときだけ使用）
    reason_rakuten: Optional[str] = None
    reason_matsui: Optional[str] = None
    reason_sbi: Optional[str] = None
    

# =========================================================
# 1銘柄処理
# =========================================================

def _work_one(
    user,
    code: str,
    nbars: int,
    filter_stats: Optional[Dict[str, int]] = None,
) -> Optional[Tuple[PickItem, Dict[str, Any]]]:
    """
    単一銘柄について、価格→特徴量→スコア→Entry/TP/SL→Sizing→理由 まで全部まとめて計算。
    sizing_meta には risk_pct / lot_size を入れて返す。
    filter_stats が渡されていれば、picks_filters によるスキップ理由ごとの件数を集計する。
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

        # --- 仕手株・流動性などのフィルタリング層 ---
        if picks_check_all is not None and FilterContext is not None:
            try:
                ctx = FilterContext(
                    code=str(code),
                    feat=feat.iloc[-1].to_dict(),
                    last=last,
                    atr=atr,
                )
                decision = picks_check_all(ctx)
                if decision and getattr(decision, "skip", False):
                    # フィルタ理由ごとの件数カウント
                    if filter_stats is not None:
                        reason = getattr(decision, "reason_code", None) or "SKIP"
                        filter_stats[reason] = filter_stats.get(reason, 0) + 1

                    if BUILD_LOG:
                        rc = getattr(decision, "reason_code", None)
                        rt = getattr(decision, "reason_text", None)
                        print(f"[picks_build] {code}: filtered out ({rc}) {rt}")
                    return None
            except Exception as ex:
                if filter_stats is not None:
                    filter_stats["filter_error"] = filter_stats.get("filter_error", 0) + 1
                if BUILD_LOG:
                    print(f"[picks_build] {code}: filter error {ex}")

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
                reasons_feat = _build_reasons_features(feat, last, atr)
                rs, concern = make_ai_reasons(reasons_feat)
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

        # 楽天
        item.qty_rakuten = sizing.get("qty_rakuten")
        item.required_cash_rakuten = sizing.get("required_cash_rakuten")
        item.est_pl_rakuten = sizing.get("est_pl_rakuten")
        item.est_loss_rakuten = sizing.get("est_loss_rakuten")

        # 松井
        item.qty_matsui = sizing.get("qty_matsui")
        item.required_cash_matsui = sizing.get("required_cash_matsui")
        item.est_pl_matsui = sizing.get("est_pl_matsui")
        item.est_loss_matsui = sizing.get("est_loss_matsui")

        # ★ SBI
        item.qty_sbi = sizing.get("qty_sbi")
        item.required_cash_sbi = sizing.get("required_cash_sbi")
        item.est_pl_sbi = sizing.get("est_pl_sbi")
        item.est_loss_sbi = sizing.get("est_loss_sbi")

        # 共通メッセージ
        reasons_text = sizing.get("reasons_text")
        item.reasons_text = reasons_text if reasons_text else None

        # 証券会社別の見送り理由（0株のときにテンプレートが表示）
        item.reason_rakuten = sizing.get("reason_rakuten_msg") or ""
        item.reason_matsui = sizing.get("reason_matsui_msg") or ""
        item.reason_sbi = sizing.get("reason_sbi_msg") or ""

        sizing_meta = {
            "risk_pct": sizing.get("risk_pct"),
            "lot_size": sizing.get("lot_size"),
        }
        return item, sizing_meta

    except Exception as e:
        print(f"[picks_build] work error for {code}: {e}")
        if filter_stats is not None:
            filter_stats["work_error"] = filter_stats.get("work_error", 0) + 1
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
        stockmaster_total = len(codes)

        if not codes:
            print("[picks_build] universe empty → 空JSON出力")
            self._emit(
                [],
                [],
                mode="full",
                style=style,
                horizon=horizon,
                universe=universe,
                topk=topk,
                meta_extra={
                    "stockmaster_total": stockmaster_total,
                    "filter_stats": {},
                },
            )
            return

        if BUILD_LOG:
            print(f"[picks_build] start FULL universe={universe} codes={stockmaster_total}")

        User = get_user_model()
        user = User.objects.first()

        items: List[PickItem] = []
        meta_extra: Dict[str, Any] = {}

        # フィルタ理由ごとの削除件数カウンタ
        filter_stats: Dict[str, int] = {}

        for code in codes:
            res = _work_one(user, code, nbars=nbars, filter_stats=filter_stats)
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

        # ---- セクターバイアス・サイズバイアス適用（あれば） ----
        if apply_bias_all is not None and items:
            try:
                apply_bias_all(items)
            except Exception as ex:
                if BUILD_LOG:
                    print(f"[picks_build] bias error: {ex}")

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
            print(
                f"[picks_build] done stockmaster_total={stockmaster_total} "
                f"total={len(items)} topk={len(top_items)}"
            )

        # 追加メタ（総StockMaster件数 & フィルタ別削除件数）
        meta_extra["stockmaster_total"] = stockmaster_total
        meta_extra["filter_stats"] = filter_stats

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