# -*- coding: utf-8 -*-
"""
aiapp.services.macro_regime

指数・為替・先物などのベンチマークから
「相場レジーム（日本株 / 米国株 / 為替 / ボラ / 総合）」を計算して
MacroRegimeSnapshot に保存するサービス群。

公開関数:
- ensure_benchmark_master()
- sync_benchmark_prices(days=365)
- build_macro_regime_snapshot(days=365, cfg=None) -> MacroRegimeSnapshot
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from django.db import transaction
from django.utils import timezone

from ..models.macro import BenchmarkMaster, BenchmarkPrice, MacroRegimeSnapshot
from ..models.features import make_features, FeatureConfig


# =========================================================
# 設定
# =========================================================

@dataclass(frozen=True)
class RegimeConfig:
    """
    マクロレジーム計算用の設定。
    """
    lookback_days: int = 180       # DB から引き出す日数
    feature_lookback: int = 90     # 特徴量計算に最低欲しい日数（今は参考値）


# 使うベンチマークのデフォルト定義
BENCHMARK_DEFAULTS = [
    # 日本株
    {"code": "NK225", "name": "日経平均", "kind": "INDEX_JP", "symbol": "^N225",  "sort_order": 10},
    # TOPIX は yfinance で ^TOPX が不安定なため ETF 1306.T を採用
    {"code": "TOPIX", "name": "TOPIX", "kind": "INDEX_JP", "symbol": "1306.T",   "sort_order": 11},
    # 米国株
    {"code": "SPX",   "name": "S&P500",   "kind": "INDEX_US", "symbol": "^GSPC", "sort_order": 20},
    {"code": "NDX",   "name": "NASDAQ100","kind": "INDEX_US", "symbol": "^NDX",  "sort_order": 21},
    # 為替
    {"code": "USDJPY","name": "ドル円",   "kind": "FX",       "symbol": "JPY=X", "sort_order": 30},
    # ボラティリティ
    {"code": "VIX",   "name": "VIX指数",  "kind": "VOL",      "symbol": "^VIX",  "sort_order": 40},
]


# =========================================================
# マスタ整備
# =========================================================

@transaction.atomic
def ensure_benchmark_master() -> None:
    """
    ベンチマークマスタを登録/更新する。
    既存レコードがあれば symbol や sort_order を上書き。
    """
    for item in BENCHMARK_DEFAULTS:
        BenchmarkMaster.objects.update_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "kind": item["kind"],
                "symbol": item["symbol"],
                "is_active": True,
                "sort_order": item["sort_order"],
            },
        )


# =========================================================
# yfinance → BenchmarkPrice
# =========================================================

def _download_ohlcv(symbol: str, days: int) -> pd.DataFrame:
    """
    yfinance から単一ティッカーの OHLCV を取得。
    単一ティッカー前提なので MultiIndex ではなく通常の列構造を想定。
    """
    df = yf.download(
        symbol,
        period=f"{days}d",
        interval="1d",
        auto_adjust=False,
        progress=False,
    )
    if df is None or df.empty:
        print(f"[_download_ohlcv] empty data: {symbol}")
        return pd.DataFrame()

    # 列名のゆらぎを吸収して Open / High / Low / Close / Volume を抽出
    cols: Dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if "open" in key and "adj" not in key:
            cols["Open"] = col
        elif "high" in key:
            cols["High"] = col
        elif "low" in key:
            cols["Low"] = col
        elif "close" in key and "adj" not in key:
            cols["Close"] = col
        elif "volume" in key or key == "vol":
            cols["Volume"] = col

    out = pd.DataFrame(index=df.index.copy())
    for std in ["Open", "High", "Low", "Close", "Volume"]:
        src = cols.get(std)
        if src is not None:
            out[std] = pd.to_numeric(df[src], errors="coerce")
        else:
            out[std] = np.nan

    out = out.dropna(subset=["Close"], how="all")
    return out


@transaction.atomic
def sync_benchmark_prices(days: int = 365) -> None:
    """
    BenchmarkMaster に登録されている全ベンチマークについて、
    直近 days 日分の価格を取得し BenchmarkPrice に upsert する。
    """
    ensure_benchmark_master()
    masters = BenchmarkMaster.objects.filter(is_active=True)

    for bm in masters:
        try:
            df = _download_ohlcv(bm.symbol, days=days)
        except Exception as e:  # pragma: no cover - デバッグ用
            print(f"[sync_benchmark_prices] download error {bm.code}: {e}")
            continue

        if df.empty:
            continue

        for idx, row in df.iterrows():
            d: date = idx.date()
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


# =========================================================
# DB → pandas.DataFrame
# =========================================================

def _load_price_df(code: str, lookback: int) -> pd.DataFrame:
    """
    BenchmarkPrice → pandas.DataFrame(Open,High,Low,Close,Volume)
    """
    try:
        bm = BenchmarkMaster.objects.get(code=code, is_active=True)
    except BenchmarkMaster.DoesNotExist:
        return pd.DataFrame()

    qs = (
        BenchmarkPrice.objects.filter(benchmark=bm)
        .order_by("-date")
        .values("date", "open", "high", "low", "close", "volume")
    )[:lookback]

    rows = list(qs)
    if not rows:
        return pd.DataFrame()

    rows.reverse()  # 昇順に並べ替え
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df = df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    return df


# =========================================================
# 個別レジーム計算ヘルパ
# =========================================================

def _trend_from_features(feat: pd.DataFrame) -> Tuple[Optional[float], str]:
    """
    MA と SLOPE から -1〜+1 のトレンドスコアとラベルを返す。
    """
    if feat is None or feat.empty:
        return None, ""

    last = feat.iloc[-1]
    close = last.get("Close")
    ma_mid = last.get("MA25") if "MA25" in feat.columns else last.get("MA20")
    ma_short = last.get("MA5")
    slope = last.get("SLOPE_25") if "SLOPE_25" in feat.columns else last.get("SLOPE_20")

    score = 0.0

    if pd.notna(close) and pd.notna(ma_mid):
        if close > ma_mid:
            score += 0.4
        else:
            score -= 0.4

    if pd.notna(ma_short) and pd.notna(ma_mid):
        if ma_short > ma_mid:
            score += 0.3
        else:
            score -= 0.3

    if pd.notna(slope):
        # 傾きを 0.3 係数で反映（slope は -1〜+1 を想定）
        score += float(slope) * 0.3

    # clamp
    score = max(-1.0, min(1.0, score))

    if score > 0.25:
        label = "UP"
    elif score < -0.25:
        label = "DOWN"
    else:
        label = "FLAT"

    return score, label


def _fx_regime_from_features(feat: pd.DataFrame) -> Tuple[Optional[float], str]:
    """
    ドル円の 20日リターン + 傾きから、円安/円高レジームを算出。
    """
    if feat is None or feat.empty:
        return None, ""

    last = feat.iloc[-1]
    ret20 = last.get("RET_20")
    slope = last.get("SLOPE_25") if "SLOPE_25" in feat.columns else last.get("SLOPE_20")

    score = 0.0
    if pd.notna(ret20):
        # 20日リターンを強めに反映
        score += float(ret20) * 3.0
    if pd.notna(slope):
        score += float(slope) * 0.5

    score = max(-1.0, min(1.0, score))

    if score > 0.3:
        label = "YEN_WEAK"   # 円安方向
    elif score < -0.3:
        label = "YEN_STRONG"  # 円高方向
    else:
        label = "NEUTRAL"

    return score, label


def _vol_regime_from_features(feat: pd.DataFrame) -> Tuple[Optional[float], str]:
    """
    VIX の BB_Z などからボラティリティレジームを推定。
    """
    if feat is None or feat.empty:
        return None, ""

    last = feat.iloc[-1]
    z = last.get("BB_Z")

    if pd.isna(z):
        return None, ""

    z = float(z)
    # z は 0 〜 2 程度が多い想定で正規化
    score = max(-1.0, min(1.0, (z - 0.5) / 2.0))

    if z < 0.0:
        label = "CALM"
    elif z < 1.0:
        label = "ELEVATED"
    else:
        label = "HIGH"

    return score, label


def _build_detail_json(
    jp_feat: pd.DataFrame,
    us_feat: pd.DataFrame,
    fx_feat: pd.DataFrame,
    vol_feat: pd.DataFrame,
) -> Dict:
    """
    デバッグ・可視化用の detail_json を構築。
    各ゾーンの最終行近辺の指標を軽く詰め込む。
    """
    def last_snapshot(feat: pd.DataFrame, extra_cols=None):
        if feat is None or feat.empty:
            return {}
        extra_cols = extra_cols or []
        cols = [
            "Close",
            "MA5",
            "MA25",
            "MA50",
            "RET_5",
            "RET_20",
            "SLOPE_5",
            "SLOPE_25",
            "BB_Z",
        ]
        cols += extra_cols
        out = {}
        last = feat.iloc[-1]
        for c in cols:
            if c in feat.columns:
                v = last.get(c)
                if pd.notna(v):
                    out[c] = float(v)
        return out

    return {
        "jp": last_snapshot(jp_feat),
        "us": last_snapshot(us_feat),
        "fx": last_snapshot(fx_feat),
        "vol": last_snapshot(vol_feat, extra_cols=["ATR14"]),
    }


# =========================================================
# メイン: マクロレジーム計算
# =========================================================

@transaction.atomic
def build_macro_regime_snapshot(days: int = 365, cfg: Optional[RegimeConfig] = None) -> MacroRegimeSnapshot:
    """
    直近のベンチマーク価格からマクロレジームを集計し、
    MacroRegimeSnapshot を upsert して返す。

    - sync_benchmark_prices(days) を内部で呼び出す
    - 日本株 / 米国株 / 為替 / ボラ / 総合レジーム を算出
    - summary フィールドに 1 行サマリを保存
    """
    cfg = cfg or RegimeConfig()
    sync_benchmark_prices(days=days)

    # ---- 価格 DF をロード ----
    jp_df_nk = _load_price_df("NK225", cfg.lookback_days)
    jp_df_tp = _load_price_df("TOPIX", cfg.lookback_days)
    us_df_spx = _load_price_df("SPX", cfg.lookback_days)
    us_df_ndx = _load_price_df("NDX", cfg.lookback_days)
    fx_df = _load_price_df("USDJPY", cfg.lookback_days)
    vol_df = _load_price_df("VIX", cfg.lookback_days)

    # スナップショット日付：日本株があれば日経の最終日、なければ US → FX → 今日
    base_df = None
    for df in [jp_df_nk, jp_df_tp, us_df_spx, us_df_ndx, fx_df, vol_df]:
        if df is not None and not df.empty:
            base_df = df
            break

    if base_df is not None and not base_df.empty:
        target_date: date = base_df.index.max().date()
    else:
        target_date = timezone.localdate()

    # ---- 特徴量を計算 ----
    feat_cfg = FeatureConfig(
        ma_short=5,
        ma_mid=25,
        ma_long=75,
        slope_short=5,
        slope_mid=25,
    )

    def make_feat(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        return make_features(df, cfg=feat_cfg)

    jp_feat_nk = make_feat(jp_df_nk)
    jp_feat_tp = make_feat(jp_df_tp)
    fx_feat = make_feat(fx_df)
    vol_feat = make_feat(vol_df)

    # 米国株は SPX + NDX を統合（Close の平均）してから特徴量を再計算
    us_feat_spx = make_feat(us_df_spx)
    us_feat_ndx = make_feat(us_df_ndx)
    if not us_feat_spx.empty and not us_feat_ndx.empty:
        aligned = us_feat_spx[["Close"]].join(
            us_feat_ndx[["Close"]], how="inner", lsuffix="_spx", rsuffix="_ndx"
        )
        if not aligned.empty:
            avg_close = aligned.mean(axis=1)
            tmp = pd.DataFrame({"Close": avg_close})
            us_feat = make_features(tmp, cfg=feat_cfg)
        else:
            us_feat = us_feat_spx
    else:
        us_feat = us_feat_spx if not us_feat_spx.empty else us_feat_ndx

    # ---- トレンドスコア & ラベル ----
    # 日本株：日経 + TOPIX をざっくり平均
    jp_score_1, jp_label_1 = _trend_from_features(jp_feat_nk)
    jp_score_2, jp_label_2 = _trend_from_features(jp_feat_tp)

    if jp_score_1 is not None and jp_score_2 is not None:
        jp_trend_score = (jp_score_1 + jp_score_2) / 2.0
    else:
        jp_trend_score = jp_score_1 if jp_score_1 is not None else jp_score_2

    # ラベルは優先度: どちらかが UP/DOWN の場合はそちら、両方 FLAT or 片方 None の場合は残り
    if jp_label_1 in ("UP", "DOWN"):
        jp_trend_label = jp_label_1
    elif jp_label_2 in ("UP", "DOWN"):
        jp_trend_label = jp_label_2
    else:
        jp_trend_label = jp_label_1 or jp_label_2 or ""

    # 米国株
    us_trend_score, us_trend_label = _trend_from_features(us_feat)

    # 為替
    fx_trend_score, fx_trend_label = _fx_regime_from_features(fx_feat)

    # ボラティリティ
    vol_level, vol_label = _vol_regime_from_features(vol_feat)

    # ---- 総合レジームスコア ----
    # 日本株 40%, 米国株 30%, 為替 15%, ボラ(逆符号) 15%
    def nz(x: Optional[float]) -> float:
        return float(x) if x is not None else 0.0

    regime_score = (
        nz(jp_trend_score) * 0.4
        + nz(us_trend_score) * 0.3
        + nz(fx_trend_score) * 0.15
        - nz(vol_level) * 0.15  # ボラ上昇はリスクオフ方向
    )
    regime_score = max(-1.0, min(1.0, regime_score))

    if regime_score > 0.3:
        regime_label = "RISK_ON"
    elif regime_score < -0.3:
        regime_label = "RISK_OFF"
    else:
        regime_label = "NEUTRAL"

    # ---- summary テキスト（DB 保存用）----
    summary = (
        f"日本株: {jp_trend_label or '?'} / "
        f"米国株: {us_trend_label or '?'} / "
        f"為替: {fx_trend_label or '?'} / "
        f"ボラ: {vol_label or '?'} / "
        f"総合: {regime_label or '?'}"
    )

    # ---- detail_json ----
    jp_feat_for_detail = jp_feat_nk if not jp_feat_nk.empty else jp_feat_tp
    detail_json = _build_detail_json(jp_feat_for_detail, us_feat, fx_feat, vol_feat)

    # ---- Snapshot upsert ----
    snap, _created = MacroRegimeSnapshot.objects.update_or_create(
        date=target_date,
        defaults={
            "jp_trend_score": jp_trend_score,
            "jp_trend_label": jp_trend_label,
            "us_trend_score": us_trend_score,
            "us_trend_label": us_trend_label,
            "fx_trend_score": fx_trend_score,
            "fx_trend_label": fx_trend_label,
            "vol_level": vol_level,
            "vol_label": vol_label,
            "regime_score": regime_score,
            "regime_label": regime_label,
            "summary": summary,          # ★ ここで正式に DB に保存
            "detail_json": detail_json,
        },
    )

    return snap