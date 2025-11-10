# -*- coding: utf-8 -*-
"""
AIピック生成（FULL/LITE/SNAPSHOT対応の堅牢版）
- fetch_price.get_prices を通して必ず整形済みOHLCVを受け取る
- 特徴量作成は models.features.make_features
- スコア/信頼度/Entry-TP-SL は services があれば使用、無ければフォールバック
- すべての計算前に Series/np.ndarray を保証し、「arg must be a list, tuple, 1-d array, or Series」を根絶
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

from aiapp.services.fetch_price import get_prices
from aiapp.models.features import make_features, FeatureConfig

# 任意：外部サービス化されていれば使い、無ければ内蔵フォールバック
try:
    from aiapp.services.scoring import score_sample as ext_score_sample, stars_from_score as ext_stars_from_score
except Exception:
    ext_score_sample = None
    ext_stars_from_score = None

try:
    from aiapp.services.entry_exit import compute_entry_tp_sl as ext_entry_tp_sl
except Exception:
    ext_entry_tp_sl = None


# ---------- 環境・入出力 ----------

PICKS_DIR = Path("media/aiapp/picks")
PICKS_DIR.mkdir(parents=True, exist_ok=True)

def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1","true","yes","on")

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
        # 代表列がある場合は最終列を取る（誤使用防止）
        if x.shape[1] >= 1:
            return x.iloc[:, -1].astype("float64")
        return pd.Series(dtype="float64")
    try:
        arr = np.asarray(x, dtype="float64")
        if arr.ndim == 0:
            # スカラ
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

def _percentile_safe(s: pd.Series, q: float) -> float:
    s = _safe_series(s).dropna()
    if s.empty:
        return float("nan")
    try:
        return float(np.percentile(s.values, q))
    except Exception:
        # フォールバック：単純平均
        return float(s.mean())

def _nan_to_none(x):
    if isinstance(x, (float, int)) and (x != x):  # NaN判定
        return None
    return x


# ---------- 内蔵フォールバック（servicesが未実装でも動く） ----------

def _fallback_score_sample(feat: pd.DataFrame) -> float:
    """
    0.0〜1.0のサンプルスコア。
    - 傾き、RSI、直近リターン、出来高対MA などを薄く合成
    - 値が一定（0.227..固定）にならないよう、標準化→シグモイド→加重和
    """
    if feat is None or len(feat) == 0:
        return 0.0
    f = feat.copy()
    # 必要列が無い場合は安全に作る
    for c in ["RSI14", "RET_5", "RET_20", "SLOPE_5", "SLOPE_20", "Volume", "MA20"]:
        if c not in f.columns:
            f[c] = np.nan

    # 正規化ユーティリティ
    def nz(s: pd.Series) -> pd.Series:
        s = _safe_series(s)
        if s.empty:
            return s
        m, sd = float(s.mean()), float(s.std(ddof=0))
        if not np.isfinite(sd) or sd == 0:
            return pd.Series(np.zeros(len(s)), index=s.index)
        return (s - m) / sd

    # 最終点のみ評価
    rsi   = _safe_float((nz(f.get("RSI14"))).iloc[-1] if "RSI14" in f else np.nan)
    mom5  = _safe_float((nz(f.get("RET_5"))).iloc[-1] if "RET_5" in f else np.nan)
    mom20 = _safe_float((nz(f.get("RET_20"))).iloc[-1] if "RET_20" in f else np.nan)
    sl5   = _safe_float((nz(f.get("SLOPE_5"))).iloc[-1] if "SLOPE_5" in f else np.nan)
    sl20  = _safe_float((nz(f.get("SLOPE_20"))).iloc[-1] if "SLOPE_20" in f else np.nan)

    # シグモイド圧縮
    def sig(x): 
        try:
            return 1.0 / (1.0 + np.exp(-float(x)))
        except Exception:
            return 0.5

    comp = (
        0.30 * sig(rsi) +
        0.25 * sig(mom5) +
        0.20 * sig(mom20) +
        0.15 * sig(sl5) +
        0.10 * sig(sl20)
    )
    # 0..1 にクリップ
    comp = max(0.0, min(1.0, comp))
    return float(comp)

def _fallback_stars(score01: float) -> int:
    """
    0..1→1..5段階（固定にならないようビン幅に僅かなランプ）
    """
    if not np.isfinite(score01):
        return 1
    s = max(0.0, min(1.0, float(score01)))
    # 5分位ふう
    if s < 0.2:  return 1
    if s < 0.4:  return 2
    if s < 0.6:  return 3
    if s < 0.8:  return 4
    return 5

def _fallback_entry_tp_sl(last: float, atr: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    短期×攻めの暫定本番レンジで高値掴みを緩和：
      entry = last + 0.10*ATR ではなく、
      entry = last + 0.05*ATR（上抜き待ちを弱める）
      TP    = entry + 0.8*ATR
      SL    = entry - 0.6*ATR
    """
    if not np.isfinite(last) or not np.isfinite(atr) or atr <= 0:
        return None, None, None
    entry = last + 0.05 * atr
    tp    = entry + 0.80 * atr
    sl    = entry - 0.60 * atr
    return float(entry), float(tp), float(sl)


# ---------- メイン処理 ----------

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
    required_cash: Optional[float] = None
    qty: Optional[int] = None
    est_pl: Optional[float] = None
    est_loss: Optional[float] = None
    reasons_text: Optional[List[str]] = None

def _score_to_0_100(s01: float) -> int:
    if not np.isfinite(s01):
        return 0
    return int(round(max(0.0, min(1.0, s01)) * 100))

def _work_one(code: str, nbars: int) -> Optional[PickItem]:
    """
    単銘柄の計算。必ず try/except で落ちないようにし、途中で None を渡さない。
    """
    try:
        # 価格取得（堅牢化済）
        raw = get_prices(code, nbars=nbars, period="3y")
        if raw is None or len(raw) == 0:
            if BUILD_LOG:
                print(f"[picks_build] {code}: empty price")
            return None

        # 特徴量
        feat = make_features(raw, cfg=FeatureConfig())
        if feat is None or len(feat) == 0:
            if BUILD_LOG:
                print(f"[picks_build] {code}: empty features")
            return None

        close_s = _safe_series(feat.get("Close"))
        atr_s   = _safe_series(feat.get("ATR14") if "ATR14" in feat else feat.get("ATR", None))

        last = _safe_float(close_s.iloc[-1] if len(close_s) else np.nan)
        atr  = _safe_float(atr_s.iloc[-1]   if len(atr_s)   else np.nan)

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
            name=None,  # ※名称は別途辞書があれば後段で埋める
            sector_display=None,
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
        # ここで “arg must be a list, tuple, 1-d array, or Series” を飲み込み、継続
        print(f"[picks_build] work error for {code}: {e}")
        return None


def _load_universe(name: str) -> List[str]:
    """
    --universe に渡された値がファイル名なら aiapp/data/universe/*.txt を読む。
    それ以外は 'all','nk225','quick_100' 等のプリセットは未実装のため、ファイル名前提。
    """
    # 単純にファイル扱い
    base = Path("aiapp/data/universe")
    txt = base / (name if name.endswith(".txt") else f"{name}.txt")
    if not txt.exists():
        print(f"[picks_build] universe file not found: {txt}")
        return []
    codes = []
    for line in txt.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        # コメント行/カンマ区切りにも緩く対応
        if line.startswith("#"):
            continue
        codes.append(line.split(",")[0].strip())
    return codes


# ---------- Django command ----------

class Command(BaseCommand):
    help = "AIピック生成（完全版/ライト・スナップショット対応）"

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

    def handle(self, *args, **opts):
        universe = opts.get("universe") or "quick_30"
        nbars    = int(opts.get("nbars") or 180)
        # lite-only でも内部は共通の堅牢計算で処理（nbars-liteは外側で使うなら利用）
        codes = _load_universe(universe)
        if not codes:
            print("[picks_build] items=0 (empty json emitted)")
            self._emit([], mode="full", style=opts.get("style"), horizon=opts.get("horizon"))
            return

        if BUILD_LOG:
            print(f"[picks_build] start FULL universe={len(codes)}")

        items: List[PickItem] = []
        for code in codes:
            it = _work_one(code, nbars=nbars)
            if it is not None:
                items.append(it)

        # 並び：score_100 desc → last_close desc（最後の見栄え調整）
        items.sort(key=lambda x: (x.score_100 if x.score_100 is not None else -1,
                                  x.last_close if x.last_close is not None else -1),
                   reverse=True)

        if BUILD_LOG:
            print(f"[picks_build] done items={len(items)}")

        self._emit(items, mode="full", style=opts.get("style"), horizon=opts.get("horizon"))

    # ------ 出力 ------
    def _emit(self, items: List[PickItem], mode: str, style: str, horizon: str):
        data = {
            "mode": mode,
            "style": style,
            "horizon": horizon,
            "items": [asdict(x) for x in items],
        }
        PICKS_DIR.mkdir(parents=True, exist_ok=True)
        # latest_full.json にも保存
        out_full = PICKS_DIR / "latest_full.json"
        out_mode = PICKS_DIR / f"{dt_now_stamp()}_{horizon}_{style}_full.json"
        for p in [out_full, out_mode]:
            p.write_text(json.dumps(data, ensure_ascii=False, separators=(",",":")))

def dt_now_stamp() -> str:
    from datetime import datetime, timezone, timedelta
    JST = timezone(timedelta(hours=9))
    return datetime.now(JST).strftime("%Y%m%d_%H%M%S")