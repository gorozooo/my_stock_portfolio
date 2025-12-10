# -*- coding: utf-8 -*-
"""
aiapp.services.macro_regime

指数・為替・先物などのベンチマークを取得して、
日次の「相場レジーム」を MacroRegimeSnapshot に保存するサービス。

公開関数:
- ensure_benchmark_master()         … マスタ登録
- sync_benchmark_prices(days=365)   … ベンチマーク価格の更新
- build_macro_regime_snapshot(...)  … 1日分のレジーム集計＆保存
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from typing import Dict, List, Tuple, Optional

import logging

import numpy as np
import pandas as pd
import yfinance as yf
from django.db import transaction
from django.utils import timezone

from aiapp.models.macro import BenchmarkMaster, BenchmarkPrice, MacroRegimeSnapshot

logger = logging.getLogger(__name__)


# =========================================================
# ベンチマーク定義
# =========================================================

@dataclass(frozen=True)
class BenchmarkDef:
    code: str
    name: str
    kind: str
    symbol: str
    sort_order: int
    is_active: bool = True


BENCHMARK_DEFS: List[BenchmarkDef] = [
    # 日本株
    BenchmarkDef("NK225", "日経平均", "INDEX_JP", "^N225", 10),
    BenchmarkDef("TOPIX", "TOPIX（ETF 1306.T）", "INDEX_JP", "1306.T", 20),
    # 米国株
    BenchmarkDef("SPX", "S&P500", "INDEX_US", "^GSPC", 30),
    BenchmarkDef("NDX", "NASDAQ100", "INDEX_US", "^NDX", 40),
    # 為替
    BenchmarkDef("USDJPY", "ドル円", "FX", "JPY=X", 50),
    # ボラ
    BenchmarkDef("VIX", "VIX指数", "VOL", "^VIX", 60),
]


# =========================================================
# マスタ登録
# =========================================================

def ensure_benchmark_master() -> None:
    """
    BENCHMARK_DEFS に基づいて BenchmarkMaster を作成/更新する。
    """
    with transaction.atomic():
        for d in BENCHMARK_DEFS:
            obj, created = BenchmarkMaster.objects.update_or_create(
                code=d.code,
                defaults={
                    "name": d.name,
                    "kind": d.kind,
                    "symbol": d.symbol,
                    "is_active": d.is_active,
                    "sort_order": d.sort_order,
                },
            )
            if created:
                logger.info("[ensure_benchmark_master] created %s (%s)", obj.code, obj.symbol)
            else:
                logger.debug("[ensure_benchmark_master] updated %s (%s)", obj.code, obj.symbol)


# =========================================================
# 価格取得ヘルパ（features に頼らず生OHLCVだけ整形）
# =========================================================

def _download_ohlcv(symbol: str, days: int = 365) -> pd.DataFrame:
    """
    yfinance から日足OHLCVを取得して、標準化した DataFrame を返す。
    index: DatetimeIndex, columns: ["Open","High","Low","Close","Volume"]
    """
    period = f"{max(days, 365)}d"

    logger.info("[_download_ohlcv] download %s period=%s", symbol, period)
    df = yf.download(symbol, period=period, interval="1d", auto_adjust=False, progress=False)

    if df is None or df.empty:
        logger.warning("[_download_ohlcv] empty data: %s", symbol)
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    # ---- MultiIndex カラムならフラット化 ----
    if isinstance(df.columns, pd.MultiIndex):
        flat_cols: List[str] = []
        for col in df.columns:
            parts = [str(x) for x in col if x is not None and str(x) != ""]
            flat_cols.append("_".join(parts))
        df = df.copy()
        df.columns = flat_cols

    # ここまで来たら df.columns は一次元 Index[str]
    cols = [str(c) for c in df.columns]
    lower_map = {c.lower(): c for c in cols}

    def pick(aliases: List[str]) -> Optional[str]:
        # 完全一致
        for a in aliases:
            if a in lower_map:
                return lower_map[a]
        # "open_^ndx" みたいなプレフィックス/サフィックスを拾う
        for c in cols:
            lc = c.lower()
            for a in aliases:
                if lc.startswith(a + "_") or lc.endswith("_" + a):
                    return c
        return None

    open_col = pick(["open", "o"])
    high_col = pick(["high", "h"])
    low_col = pick(["low", "l"])
    close_col = pick(["close", "c", "adj close", "adj_close", "adjclose", "price", "last", "last_close"])
    vol_col = pick(["volume", "vol", "v"])

    out = pd.DataFrame(index=df.index.copy())

    def copy_col(out_name: str, src_col: Optional[str]) -> None:
        if src_col is None:
            out[out_name] = np.nan
        else:
            out[out_name] = pd.to_numeric(df[src_col], errors="coerce")

    copy_col("Open", open_col)
    copy_col("High", high_col)
    copy_col("Low", low_col)
    copy_col("Close", close_col)
    copy_col("Volume", vol_col)

    # Index を Datetime に揃える
    idx = pd.to_datetime(out.index, errors="coerce")
    mask = ~idx.isna()
    out = out.loc[mask]
    out.index = idx[mask]

    # 同一日重複は後勝ち & ソート
    out = out[~out.index.duplicated(keep="last")]
    out = out.sort_index()

    return out


# =========================================================
# ベンチマーク価格の同期
# =========================================================

def sync_benchmark_prices(days: int = 365) -> None:
    """
    BenchmarkMaster で is_active=True のものについて、
    直近 days 日くらいの価格を BenchmarkPrice に同期する。
    """
    ensure_benchmark_master()

    masters = BenchmarkMaster.objects.filter(is_active=True).order_by("sort_order", "code")
    if not masters.exists():
        logger.warning("[sync_benchmark_prices] no active BenchmarkMaster")
        return

    for bm in masters:
        try:
            df = _download_ohlcv(bm.symbol, days=days)
        except Exception as e:  # pragma: no cover
            logger.exception("[sync_benchmark_prices] download error %s: %s", bm.name, e)
            continue

        if df.empty:
            continue

        with transaction.atomic():
            for idx, row in df.iterrows():
                d = idx.date()
                try:
                    BenchmarkPrice.objects.update_or_create(
                        benchmark=bm,
                        date=d,
                        defaults={
                            "open": float(row["Open"]) if not pd.isna(row["Open"]) else None,
                            "high": float(row["High"]) if not pd.isna(row["High"]) else None,
                            "low": float(row["Low"]) if not pd.isna(row["Low"]) else None,
                            "close": float(row["Close"]) if not pd.isna(row["Close"]) else None,
                            "volume": float(row["Volume"]) if not pd.isna(row["Volume"]) else None,
                        },
                    )
                except Exception as e:  # pragma: no cover
                    logger.exception(
                        "[sync_benchmark_prices] save error %s %s: %s",
                        bm.code,
                        d,
                        e,
                    )


# =========================================================
# スコアリング用ヘルパ
# =========================================================

def _calc_ret(close: pd.Series, n: int) -> Optional[float]:
    if close is None or len(close) <= n:
        return None
    try:
        c_now = float(close.iloc[-1])
        c_past = float(close.iloc[-1 - n])
    except Exception:
        return None
    if c_past == 0 or np.isnan(c_now) or np.isnan(c_past):
        return None
    return (c_now / c_past) - 1.0


def _normalize_score(x: Optional[float], max_abs: float = 0.2) -> Optional[float]:
    """
    ざっくり -max_abs〜+max_abs を -1〜+1 に正規化。
    """
    if x is None:
        return None
    if np.isnan(x):
        return None
    x_clip = max(-max_abs, min(max_abs, x))
    return x_clip / max_abs


def _label_from_score(x: Optional[float], up="UP", down="DOWN", flat="FLAT") -> str:
    if x is None:
        return ""
    if x > 0.35:
        return up
    if x < -0.35:
        return down
    return flat


def _label_fx_from_score(x: Optional[float]) -> str:
    """
    ドル円: score>0 → 円安(YEN_WEAK)、score<0 → 円高(YEN_STRONG)
    """
    if x is None:
        return ""
    if x > 0.35:
        return "YEN_WEAK"
    if x < -0.35:
        return "YEN_STRONG"
    return "NEUTRAL"


def _label_vol_from_z(z: Optional[float]) -> Tuple[Optional[float], str]:
    """
    VIX を「平常 / やや高い / 高い」くらいのラベルにする。
    z ~ 0 付近を CALM とみなす。
    """
    if z is None or np.isnan(z):
        return None, ""

    if z < 0.0:
        label = "CALM"
    elif z < 1.0:
        label = "ELEVATED"
    else:
        label = "HIGH"
    return float(z), label


# =========================================================
# レジーム生成メイン
# =========================================================

def _get_price_series(code: str, days: int = 365) -> pd.Series:
    """
    BenchmarkPrice から、指定ベンチマークの Close シリーズを取得。
    """
    try:
        bm = BenchmarkMaster.objects.get(code=code)
    except BenchmarkMaster.DoesNotExist:
        return pd.Series(dtype="float64")

    qs = BenchmarkPrice.objects.filter(benchmark=bm).order_by("date")
    if not qs.exists():
        return pd.Series(dtype="float64")

    df = pd.DataFrame.from_records(
        qs.values("date", "close"),
        index="date",
    ).sort_index()
    return df["close"].astype("float64")


def build_macro_regime_snapshot(target_date: Optional[_date] = None) -> MacroRegimeSnapshot:
    """
    直近のベンチマーク価格から「相場レジーム」を計算して MacroRegimeSnapshot を1件保存し、
    そのインスタンスを返す。

    target_date が None の場合、今日 (timezone.localdate()) を使う。
    """
    if target_date is None:
        target_date = timezone.localdate()

    # 価格を最新にしておく（多少重くてもOK：日次/朝ジョブ想定）
    sync_benchmark_prices(days=365)

    # ---- 日本株ゾーン（NK225 + TOPIX） ----
    close_nk = _get_price_series("NK225", days=365)
    close_tx = _get_price_series("TOPIX", days=365)

    close_jp = pd.concat([close_nk, close_tx], axis=1)
    close_jp["mean"] = close_jp.mean(axis=1)
    jp_close = close_jp["mean"].dropna()

    ret_jp_5 = _calc_ret(jp_close, 5)
    ret_jp_20 = _calc_ret(jp_close, 20)
    if ret_jp_5 is None and ret_jp_20 is None:
        jp_score = None
    else:
        base = (ret_jp_20 or 0.0) * 0.6 + (ret_jp_5 or 0.0) * 0.4
        jp_score = _normalize_score(base, max_abs=0.2)
    jp_label = _label_from_score(jp_score, up="UP", down="DOWN", flat="FLAT")

    # ---- 米国株ゾーン（SPX + NDX） ----
    close_spx = _get_price_series("SPX", days=365)
    close_ndx = _get_price_series("NDX", days=365)

    close_us = pd.concat([close_spx, close_ndx], axis=1)
    close_us["mean"] = close_us.mean(axis=1)
    us_close = close_us["mean"].dropna()

    ret_us_5 = _calc_ret(us_close, 5)
    ret_us_20 = _calc_ret(us_close, 20)
    if ret_us_5 is None and ret_us_20 is None:
        us_score = None
    else:
        base_us = (ret_us_20 or 0.0) * 0.6 + (ret_us_5 or 0.0) * 0.4
        us_score = _normalize_score(base_us, max_abs=0.2)
    us_label = _label_from_score(us_score, up="UP", down="DOWN", flat="FLAT")

    # ---- 為替ゾーン（ドル円） ----
    close_fx = _get_price_series("USDJPY", days=365)
    ret_fx_20 = _calc_ret(close_fx, 20)
    fx_score = _normalize_score(ret_fx_20, max_abs=0.1) if ret_fx_20 is not None else None
    fx_label = _label_fx_from_score(fx_score)

    # ---- ボラゾーン（VIX） ----
    close_vix = _get_price_series("VIX", days=365)
    if len(close_vix) >= 60:
        last_vix = float(close_vix.iloc[-1])
        base = close_vix.iloc[-60:]
        mu = float(base.mean())
        sd = float(base.std(ddof=0) or 1.0)
        z_vix = (last_vix - mu) / sd
    else:
        z_vix = None

    vol_level, vol_label = _label_vol_from_z(z_vix)

    # ---- 総合レジーム ----
    components = []
    if jp_score is not None:
        components.append(jp_score)
    if us_score is not None:
        components.append(us_score)

    if components:
        eq_score = float(np.mean(components))
    else:
        eq_score = None

    if eq_score is None:
        regime_score = None
    else:
        regime_score = eq_score
        if vol_level is not None:
            regime_score = regime_score - float(vol_level) * 0.3
        if fx_score is not None:
            regime_score = regime_score + float(fx_score) * 0.2
        regime_score = max(-1.0, min(1.0, regime_score))

    regime_label = _label_from_score(regime_score, up="RISK_ON", down="RISK_OFF", flat="NEUTRAL")

    # ---- detail_json + summary ----
    detail: Dict[str, Dict[str, object]] = {}

    def _latest_val(s: pd.Series) -> Optional[float]:
        if s is None or s.empty:
            return None
        v = float(s.iloc[-1])
        return v if not np.isnan(v) else None

    detail["JP"] = {
        "close_nk225": _latest_val(close_nk),
        "close_topix": _latest_val(close_tx),
        "ret_5": ret_jp_5,
        "ret_20": ret_jp_20,
        "score": jp_score,
        "label": jp_label,
    }
    detail["US"] = {
        "close_spx": _latest_val(close_spx),
        "close_ndx": _latest_val(close_ndx),
        "ret_5": ret_us_5,
        "ret_20": ret_us_20,
        "score": us_score,
        "label": us_label,
    }
    detail["FX"] = {
        "close_usdjpy": _latest_val(close_fx),
        "ret_20": ret_fx_20,
        "score": fx_score,
        "label": fx_label,
    }
    detail["VOL"] = {
        "close_vix": _latest_val(close_vix),
        "z_vix": z_vix,
        "level": vol_level,
        "label": vol_label,
    }

    summary_parts: List[str] = []
    if jp_label:
        summary_parts.append(f"日本株: {jp_label}")
    if us_label:
        summary_parts.append(f"米国株: {us_label}")
    if fx_label:
        summary_parts.append(f"為替: {fx_label}")
    if vol_label:
        summary_parts.append(f"ボラ: {vol_label}")
    if regime_label:
        summary_parts.append(f"総合: {regime_label}")
    summary = " / ".join(summary_parts)

    with transaction.atomic():
        snap, _created = MacroRegimeSnapshot.objects.update_or_create(
            date=target_date,
            defaults={
                "jp_trend_score": jp_score,
                "jp_trend_label": jp_label,
                "us_trend_score": us_score,
                "us_trend_label": us_label,
                "fx_trend_score": fx_score,
                "fx_trend_label": fx_label,
                "vol_level": vol_level,
                "vol_label": vol_label,
                "regime_score": regime_score,
                "regime_label": regime_label,
                "detail_json": detail,
                "summary": summary,
            },
        )

    logger.info("[build_macro_regime_snapshot] %s %s", snap.date, snap.summary)
    return snap