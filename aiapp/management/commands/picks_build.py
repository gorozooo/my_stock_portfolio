# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import math
import time
import pathlib
import datetime as dt
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from aiapp.models import StockMaster
from aiapp.services.fetch_price import get_prices
from aiapp.models.features import compute_features
from aiapp.models.scoring import score_sample

# ──────────────────────────────────────────────────────────────────────────────
#  設定
# ──────────────────────────────────────────────────────────────────────────────
PICKS_DIR = pathlib.Path(getattr(settings, "MEDIA_ROOT", "media")) / "aiapp" / "picks"
UNIVERSE_DIR = pathlib.Path("aiapp/data/universe")

# しきい値/挙動（環境変数でチューニング可能）
MIN_SCORE = float(os.getenv("AIAPP_MIN_SCORE", "0.0"))         # LITE時の通過閾値（通常は0.0）
REQUIRE_TREND = bool(int(os.getenv("AIAPP_REQUIRE_TREND", "0")))
SKIP_LIQ = bool(int(os.getenv("AIAPP_SKIP_LIQ", "1")))          # 流動性スキップ（現状未使用フラグ）
ALLOW_ETF = bool(int(os.getenv("AIAPP_ALLOW_ETF", "1")))
BUILD_LOG = bool(int(os.getenv("AIAPP_BUILD_LOG", "0")))        # 各銘柄ログ
FUTURE_TIMEOUT = os.getenv("AIAPP_FUTURE_TIMEOUT_SEC", "").strip()
FUTURE_TIMEOUT_SEC: Optional[float] = None if (FUTURE_TIMEOUT in ("", "0", "None", "none")) else float(FUTURE_TIMEOUT)

MAX_WORKERS = max(1, int(os.getenv("AIAPP_BUILD_WORKERS", "8")))

# ──────────────────────────────────────────────────────────────────────────────
#  ユーティリティ
# ──────────────────────────────────────────────────────────────────────────────
def _ensure_dir(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _load_universe(name: str, sample: int | None, head: int | None) -> list[tuple[str, str]]:
    """ユニバースを DB or テキストから読み込み。"""
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
        # シンボリックリンク作成（不可環境では下のコピーでフォールバック）
        dst.symlink_to(src.name)
    except Exception:
        try:
            dst.write_bytes(src.read_bytes())
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
#  DataFrame 正規化系
# ──────────────────────────────────────────────────────────────────────────────
_OHLCV_CANDIDATES = {
    "open": ("open", "Open", "OPEN", ("Open", ""), ("open", "")),
    "high": ("high", "High", "HIGH", ("High", ""), ("high", "")),
    "low": ("low", "Low", "LOW", ("Low", ""), ("low", "")),
    "close": ("close", "Close", "CLOSE", ("Close", ""), ("close", "")),
    "volume": ("volume", "Volume", "VOL", "Vol", ("Volume", ""), ("volume", "")),
}

def _pick_column(df: pd.DataFrame, keys) -> Optional[pd.Series]:
    """単純列 or MultiIndex 列から候補名で拾う。見つからなければ None。"""
    for k in keys:
        if isinstance(k, tuple) and hasattr(df.columns, "levels"):
            # MultiIndex 対応
            if k in df.columns:
                return df[k]
        else:
            if k in df.columns:
                return df[k]
    # 大文字小文字正規化探索
    lower_map = {str(c).lower(): c for c in df.columns}
    for k in keys:
        if isinstance(k, tuple):
            # MultiIndex 文字列比較はスキップ
            continue
        if str(k).lower() in lower_map:
            return df[lower_map[str(k).lower()]]
    return None

def _normalize_ohlcv(raw: pd.DataFrame) -> Optional[pd.DataFrame]:
    """入力 DataFrame から Open/High/Low/Close/Volume を抽出し、標準小文字列にして返す。"""
    if raw is None or len(raw) == 0:
        return None
    df = raw.copy()

    cols = {}
    for std, cands in _OHLCV_CANDIDATES.items():
        s = _pick_column(df, cands)
        if s is None:
            cols[std] = pd.Series(np.nan, index=df.index)
        else:
            cols[std] = pd.to_numeric(s, errors="coerce")

    out = pd.DataFrame(cols)
    out.index = pd.to_datetime(df.index, errors="coerce")
    out = out.sort_index()
    # 価格の基本埋め（終値→始値、H/L を Open/Close から埋め）
    out["close"] = out["close"].ffill()
    out["open"] = out["open"].fillna(out["close"])
    out["high"] = out["high"].fillna(out[["open", "close"]].max(axis=1))
    out["low"] = out["low"].fillna(out[["open", "close"]].min(axis=1))
    out["volume"] = out["volume"].fillna(0)
    # 不要行削除
    out = out.dropna(subset=["close"])
    return out if len(out) else None

def _safe_tail_float(s: pd.Series) -> Optional[float]:
    """末尾値を float で安全取得（NaNなら None）。"""
    if s is None or len(s) == 0:
        return None
    v = s.iloc[-1]
    try:
        f = float(v)
        if np.isnan(f):
            return None
        return f
    except Exception:
        return None

def _pct_change(series: pd.Series, periods: int) -> pd.Series:
    """pandas FutureWarning 回避（fill_method=None 明示）。"""
    return series.pct_change(periods=periods, fill_method=None).replace([np.inf, -np.inf], np.nan)

# ──────────────────────────────────────────────────────────────────────────────
#  スコア/星/エントリー系（短期×攻め 暫定本番）
# ──────────────────────────────────────────────────────────────────────────────
def _score_to_0_100(s: Optional[float]) -> int:
    """score_sampleの出力（だいたい -5～+5 を想定）を 0..100 にマップ。Noneなら 0。"""
    if s is None or np.isnan(s):
        return 0
    # 中心50、1.0あたり+10点（例: 0.0→50, +2.0→70, -2.0→30）
    val = 50 + s * 10.0
    return int(max(0, min(100, round(val))))

def _score_to_stars(s100: int) -> int:
    """0..100 を 1..5 星に段階化。"""
    # 0-19=1, 20-39=2, 40-59=3, 60-79=4, 80-100=5
    if s100 < 20: return 1
    if s100 < 40: return 2
    if s100 < 60: return 3
    if s100 < 80: return 4
    return 5

def _entry_tp_sl_aggressive_short(last: float, atr: Optional[float]) -> Tuple[float, float, float]:
    """
    短期×攻めの暫定本番版:
      - Entry: last + clamp(0.001*last, 0.05*ATR, 0.003*last)  ※0.10%～0.30%の範囲でATRを反映
      - TP   : last + max(0.012*last, 0.8*ATR)
      - SL   : last - max(0.007*last, 0.5*ATR)
    """
    if atr is None or np.isnan(atr):
        atr = 0.0
    add_entry = max(min(0.05 * atr, 0.003 * last), 0.001 * last)
    tp_add = max(0.012 * last, 0.8 * atr)
    sl_sub = max(0.007 * last, 0.5 * atr)
    entry = round(last + add_entry, 1)
    tp = round(last + tp_add, 1)
    sl = round(max(1.0, last - sl_sub), 1)
    return entry, tp, sl

# ──────────────────────────────────────────────────────────────────────────────
#  ビルド本体
# ──────────────────────────────────────────────────────────────────────────────
def _build_items(
    codes: List[Tuple[str, str]],
    nbars: int,
    style: str,
    horizon: str,
    use_lite: bool,
) -> List[Dict]:
    """
    コア処理：銘柄ごとに価格取得→特徴量→スコア→UIアイテム化。
    """
    items: List[Dict] = []

    def work(code: str, name: str) -> Optional[Dict]:
        try:
            raw = get_prices(code, nbars)
            if raw is None or len(raw) < 2:
                return None

            # 正規化
            nd = _normalize_ohlcv(raw)
            if nd is None or len(nd) < 2:
                return None

            # 特徴量
            feat = compute_features(
                nd.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
            )
            if feat is None or len(feat) < 2:
                return None

            last = _safe_tail_float(feat["Close"])
            atr = _safe_tail_float(feat.get("ATR14") or feat.get("ATR", pd.Series(dtype="float64")))
            if last is None:
                return None

            # スコア（開発↔本番 いずれもここで一本化）
            s_raw = None
            try:
                s_raw = float(score_sample(feat, mode=style, horizon=horizon))
            except Exception:
                s_raw = None

            s100 = _score_to_0_100(s_raw)
            stars = _score_to_stars(s100)

            # LITEモードで条件絞り（最低限）
            if use_lite:
                if REQUIRE_TREND:
                    # 20日変化率がマイナスなら除外（必要に応じて）
                    t20 = _pct_change(feat["Close"], 20).iloc[-1]
                    if pd.isna(t20) or t20 <= 0:
                        return None
                if s100 < int(MIN_SCORE * 100):
                    return None

            # エントリー/TP/SL（短期×攻め 暫定本番）
            entry, tp, sl = _entry_tp_sl_aggressive_short(last, atr)

            # 理由（軽量版）
            trend_20d = float((_pct_change(feat["Close"], 20).iloc[-1] or 0.0) * 100.0) if len(feat) >= 21 else 0.0
            rs_20d = float(
                ((_pct_change(feat["Close"], 20).iloc[-1] - _pct_change(feat["Close"], 20).mean()) or 0.0) * 100.0
            ) if len(feat) >= 21 else 0.0
            vol_ratio = 0.0
            try:
                vol_ratio = float(feat["Volume"].iloc[-1] / (feat["Volume"].rolling(20).mean().iloc[-1] + 1e-9))
            except Exception:
                vol_ratio = 0.0

            # UI 用アイテム
            it = {
                "code": code,
                "name": name,
                "name_norm": name,
                "sector": "",
                "last_close": float(last),
                "entry": float(entry),
                "tp": float(tp),
                "sl": float(sl),
                "score": None if s_raw is None else round(float(s_raw), 3),
                "score_100": int(s100),
                "stars": int(stars),
                # 暫定: 100株単元での概算（将来 lot/価格帯で可変に）
                "qty": 100,
                "required_cash": int(round(last * 100)),
                "est_pl": int(round((tp - last) * 100)),
                "est_loss": int(round((last - sl) * 100)),
                "reasons": {
                    "trend_20d_pct": trend_20d,
                    "rs_20d": rs_20d,
                    "vol_signal": vol_ratio,
                    "atr14": 0.0 if atr is None else float(atr),
                },
            }

            if BUILD_LOG:
                print(f"[picks_build] {code}: last={it['last_close']} atr={it['reasons']['atr14']} "
                      f"score_raw={s_raw} score100={s100} entry={entry} tp={tp} sl={sl}")

            return it
        except Exception as e:
            if BUILD_LOG:
                print(f"[picks_build] work error for {code}: {e}")
            return None

    # 並列実行
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(work, c, n): (c, n) for c, n in codes}
        for fut in as_completed(futs, timeout=None if FUTURE_TIMEOUT_SEC is None else max(1.0, FUTURE_TIMEOUT_SEC)):
            try:
                it = fut.result(timeout=None if FUTURE_TIMEOUT_SEC is None else max(1.0, FUTURE_TIMEOUT_SEC))
                if it:
                    items.append(it)
            except Exception as e:
                # ここで落としても続行
                if BUILD_LOG:
                    print(f"[picks_build] future error: {e}")

    # スコア降順で上位10件（将来はstyleごとに可変）
    items = sorted(items, key=lambda x: x.get("score_100", 0), reverse=True)[:10]
    return items


# ──────────────────────────────────────────────────────────────────────────────
#  Django Command
# ──────────────────────────────────────────────────────────────────────────────
class Command(BaseCommand):
    help = "AIピック生成（完全版/ライト・スナップショット対応）"

    def add_arguments(self, parser):
        parser.add_argument("--universe", default="all", help="all / nk225 / quick_100 / <file name>")
        parser.add_argument("--sample", type=int, default=None)
        parser.add_argument("--head", type=int, default=None)
        parser.add_argument("--budget", type=int, default=180, help="（互換）未使用・将来用")
        parser.add_argument("--nbars", type=int, default=180)
        parser.add_argument("--nbars-lite", dest="nbars_lite", type=int, default=60, help="ライトモード時の足本数")
        parser.add_argument("--use-snapshot", dest="use_snapshot", action="store_true", help="夜間スナップショット利用")
        parser.add_argument("--lite-only", action="store_true", help="日中ライト表示用")
        parser.add_argument("--style", default="aggressive", help="aggressive/normal/defensive（暫定）")
        parser.add_argument("--horizon", default="short", help="short/mid/long（暫定）")
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **opts):
        universe = str(opts.get("universe", "all"))
        sample = opts.get("sample")
        head = opts.get("head")
        nbars = int(opts.get("nbars", 180))
        nbars_lite = int(opts.get("nbars_lite", 60))
        use_snap = bool(opts.get("use_snapshot", False))
        lite = bool(opts.get("lite_only"))
        style = str(opts.get("style") or "aggressive")
        horizon = str(opts.get("horizon") or "short")

        # 念のため小文字化（以前の tuple など混入事故を防ぐ）
        try:
            style = style.lower()
        except Exception:
            style = "aggressive"
        try:
            horizon = horizon.lower()
        except Exception:
            horizon = "short"

        _ensure_dir(PICKS_DIR)

        codes = _load_universe(universe, sample, head)
        if not codes:
            self.stdout.write(self.style.WARNING("[picks_build] universe=0"))
            # からJSONを出しておく
            p = _json_path("empty")
            p.write_text(json.dumps({"items": [], "mode": "EMPTY", "updated_at": dt.datetime.now().isoformat()}, ensure_ascii=False))
            _link_latest(p, "latest.json")
            return

        tag = f"{horizon}_{style}"
        mode_label = "LITE" if lite else ("SNAPSHOT" if use_snap else "FULL")

        if lite:
            self.stdout.write(f"[picks_build] start LITE universe={len(codes)}")
            items = _build_items(codes, nbars_lite, style, horizon, use_lite=True)
            # セクター名付与
            if items:
                sec_map = {
                    c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
                    .values_list("code", "sector_name")
                }
                for it in items:
                    it["sector"] = sec_map.get(it["code"], "")

            # 出力
            p = _json_path(f"{tag}_lite")
            p.write_text(json.dumps({
                "items": items or [],
                "mode": "LIVE-FAST",
                "updated_at": dt.datetime.now().isoformat(),
            }, ensure_ascii=False))
            _link_latest(p, "latest_lite.json")
            _link_latest(p, "latest.json")

            if items:
                self.stdout.write(f"[picks_build] done (lite) items={len(items)} -> {p}")
            else:
                self.stdout.write(self.style.WARNING("[picks_build] lite: items=0 (empty json emitted)"))
            return

        # FULL
        self.stdout.write(f"[picks_build] start FULL universe={len(codes)}")
        items = _build_items(codes, nbars, style, horizon, use_lite=False)

        # セクター名付与
        if items:
            sec_map = {
                c: s for c, s in StockMaster.objects.filter(code__in=[x["code"] for x in items])
                .values_list("code", "sector_name")
            }
            for it in items:
                it["sector"] = sec_map.get(it["code"], "")

        p = _json_path(f"{tag}_full")
        p.write_text(json.dumps({
            "items": items or [],
            "mode": mode_label,
            "updated_at": dt.datetime.now().isoformat(),
        }, ensure_ascii=False))
        _link_latest(p, "latest_full.json")
        _link_latest(p, "latest.json")

        if items:
            self.stdout.write(f"[picks_build] done (full) items={len(items)} -> {p}")
        else:
            self.stdout.write(self.style.WARNING("[picks_build] items=0 (empty json emitted)"))