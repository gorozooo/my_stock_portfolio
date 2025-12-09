# -*- coding: utf-8 -*-
"""
aiapp.services.macro_regime

指数・為替などのマクロ指標を DB に保存して、
シンプルな「レジーム（強気/弱気/レンジ・円高/円安 など）」を判定するサービス層。

役割:
  - BenchmarkMaster の初期登録（N225, TOPIX, S&P500, NASDAQ, USDJPY, VIX など）
  - yfinance から OHLCV を取得して BenchmarkPrice に保存
  - 直近のリターンやボラティリティから MacroRegimeSnapshot を計算して保存
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from django.db import transaction
from django.utils import timezone

from aiapp.models import BenchmarkMaster, BenchmarkPrice, MacroRegimeSnapshot


# =========================================================
# 設定・定義
# =========================================================

# kind の例（macro.py 側で choices として想定しているものに合わせる）
KIND_EQ_JP = "eq_jp"
KIND_EQ_US = "eq_us"
KIND_FX = "fx"
KIND_VOL = "vol"


@dataclass(frozen=True)
class BenchDef:
    code: str             # アプリ内コード（ユニーク）
    name: str             # 表示名
    kind: str             # KIND_EQ_JP / KIND_EQ_US / KIND_FX / KIND_VOL など
    symbol: str           # yfinance シンボル
    currency: str         # 通貨（"JPY", "USD" など）
    unit: str             # 単位（"index", "fx", "pts" など）
    is_active: bool = True


# 「日本株に効きそうな」代表的な指数と為替
BENCHMARK_DEFS: List[BenchDef] = [
    # --- 日本株 ---
    BenchDef(
        code="NK225",
        name="日経平均株価",
        kind=KIND_EQ_JP,
        symbol="^N225",
        currency="JPY",
        unit="index",
    ),
    BenchDef(
        code="TOPIX",
        name="TOPIX",
        kind=KIND_EQ_JP,
        symbol="^TOPX",
        currency="JPY",
        unit="index",
    ),

    # --- 米国株 ---
    BenchDef(
        code="SP500",
        name="S&P 500",
        kind=KIND_EQ_US,
        symbol="^GSPC",
        currency="USD",
        unit="index",
    ),
    BenchDef(
        code="NASDAQ100",
        name="NASDAQ 100",
        kind=KIND_EQ_US,
        symbol="^NDX",
        currency="USD",
        unit="index",
    ),

    # --- 為替 ---
    BenchDef(
        code="USDJPY",
        name="ドル円",
        kind=KIND_FX,
        symbol="JPY=X",      # yfinance は "JPY=X" が USD/JPY レート
        currency="JPY",
        unit="fx",
    ),

    # --- ボラティリティ ---
    BenchDef(
        code="VIX",
        name="VIX 指数",
        kind=KIND_VOL,
        symbol="^VIX",
        currency="USD",
        unit="index",
    ),
]


# =========================================================
# マスタ登録
# =========================================================

@transaction.atomic
def ensure_benchmark_master() -> None:
    """
    BENCHMARK_DEFS に基づいて BenchmarkMaster を作成・更新する。

    ※ モデル側に存在しないフィールド（currency, unit, kind など）は
       自動的にスキップするようにして、TypeError を避ける。
    """
    existing: Dict[str, BenchmarkMaster] = {
        bm.code: bm for bm in BenchmarkMaster.objects.all()
    }

    # モデルが実際に持っているフィールド名セット
    field_names = {f.name for f in BenchmarkMaster._meta.get_fields()}

    for bd in BENCHMARK_DEFS:
        if bd.code in existing:
            bm = existing[bd.code]
            changed = False
            update_fields: List[str] = []

            # name
            if bm.name != bd.name:
                bm.name = bd.name
                changed = True
                update_fields.append("name")

            # kind（あれば）
            if "kind" in field_names:
                current = getattr(bm, "kind", None)
                if current != bd.kind:
                    setattr(bm, "kind", bd.kind)
                    changed = True
                    update_fields.append("kind")

            # symbol
            if bm.symbol != bd.symbol:
                bm.symbol = bd.symbol
                changed = True
                update_fields.append("symbol")

            # currency（あれば）
            if "currency" in field_names:
                current = getattr(bm, "currency", None)
                if current != bd.currency:
                    setattr(bm, "currency", bd.currency)
                    changed = True
                    update_fields.append("currency")

            # unit（あれば）
            if "unit" in field_names:
                current = getattr(bm, "unit", None)
                if current != bd.unit:
                    setattr(bm, "unit", bd.unit)
                    changed = True
                    update_fields.append("unit")

            # is_active
            if bm.is_active != bd.is_active:
                bm.is_active = bd.is_active
                changed = True
                update_fields.append("is_active")

            if changed:
                if update_fields:
                    bm.save(update_fields=update_fields)
                else:
                    bm.save()
        else:
            # create 用 kwargs も「実在するフィールドだけ」を渡す
            kwargs = {
                "code": bd.code,
                "name": bd.name,
                "symbol": bd.symbol,
                "is_active": bd.is_active,
            }
            if "kind" in field_names:
                kwargs["kind"] = bd.kind
            if "currency" in field_names:
                kwargs["currency"] = bd.currency
            if "unit" in field_names:
                kwargs["unit"] = bd.unit

            BenchmarkMaster.objects.create(**kwargs)


# =========================================================
# OHLCV 取得 & 保存
# =========================================================

def _download_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    """
    yfinance から日足 OHLCV を取得。
    start, end は date。end は yfinance 仕様で「含まれない」ので +1 日して渡す。
    """
    yf_start = start.strftime("%Y-%m-%d")
    yf_end = (end + timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download(symbol, start=yf_start, end=yf_end, interval="1d", auto_adjust=False)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    # 列名を統一
    df = df.rename(columns={
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    })
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df[["open", "high", "low", "close", "volume"]].copy()


@transaction.atomic
def sync_benchmark_prices(days: int = 365) -> None:
    """
    直近 days 日分の OHLCV を各 BenchmarkMaster ごとに取得して BenchmarkPrice を更新する。
    - 既に同じ (benchmark, date) があれば上書き
    - 無ければ新規作成
    """
    today = timezone.localdate()
    start = today - timedelta(days=days)

    ensure_benchmark_master()  # 念のためマスタも揃えておく

    masters = BenchmarkMaster.objects.filter(is_active=True).order_by("code")
    for bm in masters:
        symbol = bm.symbol
        if not symbol:
            continue
        df = _download_ohlcv(symbol, start=start, end=today)
        if df.empty:
            continue

        # 1日ずつ保存
        for dt_idx, row in df.iterrows():
            d = dt_idx.date()
            BenchmarkPrice.objects.update_or_create(
                benchmark=bm,
                date=d,
                defaults={
                    "open": float(row.get("open", np.nan)) if pd.notna(row.get("open")) else None,
                    "high": float(row.get("high", np.nan)) if pd.notna(row.get("high")) else None,
                    "low": float(row.get("low", np.nan)) if pd.notna(row.get("low")) else None,
                    "close": float(row.get("close", np.nan)) if pd.notna(row.get("close")) else None,
                    "volume": int(row.get("volume")) if pd.notna(row.get("volume")) else 0,
                },
            )


# =========================================================
# レジーム判定ロジック（シンプル版）
# =========================================================

def _classify_trend_from_returns(ret_20: float, ret_60: float) -> str:
    """
    ごく簡易なトレンド判定:
      - 20日・60日リターンとも + なら "bull"
      - 20日・60日リターンとも - なら "bear"
      - それ以外は "range"
    """
    if ret_20 is None or ret_60 is None:
        return "unknown"

    if ret_20 > 0 and ret_60 > 0:
        return "bull"
    if ret_20 < 0 and ret_60 < 0:
        return "bear"
    return "range"


def _calc_returns(prices: List[float], days: int) -> Optional[float]:
    """
    終値リストから、末尾基準の days 日リターンを計算。
    prices[-1] / prices[-(days+1)] - 1 を返す。
    """
    if len(prices) <= days:
        return None
    try:
        p0 = prices[-(days + 1)]
        p1 = prices[-1]
        if p0 <= 0:
            return None
        return float(p1 / p0 - 1.0)
    except Exception:
        return None


def _get_price_series(bm_code: str, lookback_days: int = 120) -> List[float]:
    """
    指定コードの BenchmarkPrice から、直近 lookback_days 日分の終値リストを取得。
    """
    try:
        bm = BenchmarkMaster.objects.get(code=bm_code)
    except BenchmarkMaster.DoesNotExist:
        return []

    qs = (
        BenchmarkPrice.objects
        .filter(benchmark=bm)
        .order_by("date")
        .values_list("close", flat=True)
    )
    prices = [float(p) for p in qs if p is not None]
    if len(prices) > lookback_days:
        prices = prices[-lookback_days:]
    return prices


def _classify_jpy_from_usdjpy_level(level: Optional[float]) -> str:
    """
    ドル円レベルから「円高・中立・円安」をざっくり判定する簡易ロジック。
    （あとでちゃんとパラメータ化する前提の仮実装）
    """
    if level is None:
        return "unknown"
    if level < 125:
        return "yen_strong"
    if level > 155:
        return "yen_weak"
    return "yen_neutral"


@transaction.atomic
def build_macro_regime_snapshot(target_date: Optional[date] = None) -> MacroRegimeSnapshot:
    """
    MacroRegimeSnapshot を 1 日分計算・保存するメイン関数。

    手順:
      1. ベンチマーク価格が無ければ 365日分 sync（初回用の簡易フォールバック）
      2. N225 / TOPIX / S&P500 / NASDAQ / USDJPY の終値系列を取得
      3. 20日・60日リターンから eq_jp / eq_us の regime を判定
      4. USDJPY のレベルから fx_jpy_regime を判定
      5. MacroRegimeSnapshot(date=...) を upsert
    """
    if target_date is None:
        target_date = timezone.localdate()

    # 念のため、価格が全然無いとき用に軽く sync
    if not BenchmarkPrice.objects.exists():
        sync_benchmark_prices(days=365)

    # 日本株レジーム（N225 + TOPIX をざっくり代表として）
    prices_nk = _get_price_series("NK225")
    prices_tp = _get_price_series("TOPIX")
    ret20_nk = _calc_returns(prices_nk, 20)
    ret60_nk = _calc_returns(prices_nk, 60)
    ret20_tp = _calc_returns(prices_tp, 20)
    ret60_tp = _calc_returns(prices_tp, 60)

    # 2つのうち「弱い方」に寄せるイメージで平均
    jp_ret20 = None
    jp_ret60 = None
    try:
        vals20 = [v for v in [ret20_nk, ret20_tp] if v is not None]
        vals60 = [v for v in [ret60_nk, ret60_tp] if v is not None]
        jp_ret20 = float(np.mean(vals20)) if vals20 else None
        jp_ret60 = float(np.mean(vals60)) if vals60 else None
    except Exception:
        pass
    eq_jp_regime = _classify_trend_from_returns(jp_ret20, jp_ret60)

    # 米国株レジーム（S&P500 / NASDAQ100）
    prices_sp = _get_price_series("SP500")
    prices_nd = _get_price_series("NASDAQ100")
    ret20_sp = _calc_returns(prices_sp, 20)
    ret60_sp = _calc_returns(prices_sp, 60)
    ret20_nd = _calc_returns(prices_nd, 20)
    ret60_nd = _calc_returns(prices_nd, 60)

    us_ret20 = None
    us_ret60 = None
    try:
        vals20 = [v for v in [ret20_sp, ret20_nd] if v is not None]
        vals60 = [v for v in [ret60_sp, ret60_nd] if v is not None]
        us_ret20 = float(np.mean(vals20)) if vals20 else None
        us_ret60 = float(np.mean(vals60)) if vals60 else None
    except Exception:
        pass
    eq_us_regime = _classify_trend_from_returns(us_ret20, us_ret60)

    # ドル円レジーム
    prices_fx = _get_price_series("USDJPY")
    fx_level = prices_fx[-1] if prices_fx else None
    fx_jpy_regime = _classify_jpy_from_usdjpy_level(fx_level)

    # （オプション）VIX レベルもテキストで添える
    prices_vix = _get_price_series("VIX")
    vix_level = prices_vix[-1] if prices_vix else None
    if vix_level is None:
        vix_comment = "VIX: データなし"
    elif vix_level < 15:
        vix_comment = f"VIX={vix_level:.1f}（低ボラティリティ）"
    elif vix_level > 30:
        vix_comment = f"VIX={vix_level:.1f}（高ボラティリティ）"
    else:
        vix_comment = f"VIX={vix_level:.1f}"

    # ざっくりしたテキスト説明
    summary_lines: List[str] = []

    def regime_jp_label(r: str) -> str:
        if r == "bull":
            return "日本株: 強気トレンド"
        if r == "bear":
            return "日本株: 弱気トレンド"
        if r == "range":
            return "日本株: レンジ相場"
        return "日本株: 判定不能"

    def regime_us_label(r: str) -> str:
        if r == "bull":
            return "米国株: 強気トレンド"
        if r == "bear":
            return "米国株: 弱気トレンド"
        if r == "range":
            return "米国株: レンジ相場"
        return "米国株: 判定不能"

    def regime_fx_label(r: str) -> str:
        if r == "yen_strong":
            return "為替: 円高方向（ドル円やや下落）"
        if r == "yen_weak":
            return "為替: 円安方向（ドル円やや上昇）"
        if r == "yen_neutral":
            return "為替: 中立圏"
        return "為替: 判定不能"

    summary_lines.append(regime_jp_label(eq_jp_regime))
    summary_lines.append(regime_us_label(eq_us_regime))
    summary_lines.append(regime_fx_label(fx_jpy_regime))
    summary_lines.append(vix_comment)

    summary_text = " / ".join(summary_lines)

    # upsert
    snap, _created = MacroRegimeSnapshot.objects.update_or_create(
        date=target_date,
        defaults={
            "eq_jp_regime": eq_jp_regime,
            "eq_us_regime": eq_us_regime,
            "fx_jpy_regime": fx_jpy_regime,
            "summary": summary_text,
        },
    )
    return snap