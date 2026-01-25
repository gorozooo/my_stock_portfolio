# -*- coding: utf-8 -*-
"""
ファイル: scripts/daytrade_backtest_multi_simple.py

目的（かんたんテスト / ワンタップ）
- 複数銘柄 × 過去N営業日（20/60/120）で、デイトレ5分足バックテストを回す。
- 戦略ロジックは一切変えない（既存: VWAPPullbackLongStrategy のまま）。
- 0トレの日が出るのは仕様。銘柄数を増やして「回る」ようにする。

【追加: 自動銘柄選定（JPX全銘柄→フィルタ→上位N）】
- tickers を渡さずに --auto を指定すると、自動で銘柄を選ぶ。
- StockMaster が使える場合：DBベースでユニバース（価格/時価総額/平均売買代金）→流動性上位→
  「5分足キャッシュ有無ではなく、必要なら force_refresh=True で取りに行く」→上位Nを確定。
- StockMaster が使えない場合：portfolio/data/tse_list.csv を読み、
  直近1営業日だけ 5分足を force_refresh=True で取得トライし、流動性（close*volume）上位から選ぶ（フォールバック）

実行例:
  # 手動
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_backtest_multi_simple.py 20 3023 6946 9501

  # 自動（全銘柄→選定→上位40）
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_backtest_multi_simple.py 20 --auto --top 40

  # 自動（フォールバックでも早く回したい時）
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_backtest_multi_simple.py 20 --auto --top 40 --scan-limit 2000
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

import numpy as np
import pandas as pd
from django.conf import settings

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.bars_5m_daytrade import load_daytrade_5m_bars
from aiapp.services.daytrade.bar_adapter_5m import df_to_bars_5m
from aiapp.services.daytrade.backtest_runner import run_backtest_one_day
from aiapp.services.daytrade.risk_math import calc_risk_budget_yen

# ユニバースフィルタ設定（picks_filter の dataclass を流用）
from aiapp.services.picks_filter import UniverseFilterConfig

# ★重要：StockMaster の import は実体に合わせる（aiapp/models/master.py）
try:
    from aiapp.models.master import StockMaster  # type: ignore
except Exception:
    StockMaster = None  # type: ignore


@dataclass
class Agg:
    days: int = 0
    traded_days: int = 0
    total_trades: int = 0
    total_pnl: int = 0
    sum_r: float = 0.0
    wins: int = 0
    losses: int = 0
    max_dd_yen: int = 0  # 最小値（マイナス）を保持


def _last_n_bdays_jst(n: int, end_d: date | None = None) -> List[date]:
    """過去N営業日（簡易：平日のみ）。"""
    if end_d is None:
        end_d = date.today()
    ds = pd.bdate_range(end=end_d, periods=n).to_pydatetime()
    return [d.date() for d in ds]


def _update_agg(agg: Agg, day_res) -> None:
    agg.days += 1
    agg.total_pnl += int(day_res.pnl_yen)
    agg.total_trades += int(len(day_res.trades))
    if len(day_res.trades) > 0:
        agg.traded_days += 1

    for tr in day_res.trades:
        r = float(getattr(tr, "r", 0.0))
        agg.sum_r += r
        if int(getattr(tr, "pnl_yen", 0)) >= 0:
            agg.wins += 1
        else:
            agg.losses += 1

    try:
        agg.max_dd_yen = min(int(agg.max_dd_yen), int(day_res.max_drawdown_yen))
    except Exception:
        pass


def _fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"


def _safe_float(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _safe_int(x, default: int = 0) -> int:
    try:
        if x is None:
            return int(default)
        return int(x)
    except Exception:
        return int(default)


def _get_exit_reason(tr) -> str:
    r = getattr(tr, "exit_reason", None)
    if r is None:
        return "unknown"
    s = str(r).strip()
    return s if s else "unknown"


def _slice_bars_for_trade(bars, entry_dt: datetime, exit_dt: datetime):
    """entry_dt〜exit_dt の間のバーを抽出。"""
    if not bars or entry_dt is None or exit_dt is None:
        return []
    out = []
    for b in bars:
        try:
            if b.dt >= entry_dt and b.dt <= exit_dt:
                out.append(b)
        except Exception:
            continue
    return out


def _trade_mfe_mae_yen_long(tr, bars_slice) -> Tuple[int, int]:
    """ロング前提で MFE/MAE を円で計算。"""
    entry_price = _safe_float(getattr(tr, "entry_price", 0.0))
    qty = _safe_int(getattr(tr, "qty", 0))
    if qty <= 0 or entry_price <= 0:
        return (0, 0)

    if not bars_slice:
        exit_price = _safe_float(getattr(tr, "exit_price", entry_price))
        pnl = int((exit_price - entry_price) * qty)
        return (max(pnl, 0), min(pnl, 0))

    highs = []
    lows = []
    for b in bars_slice:
        highs.append(_safe_float(getattr(b, "high", np.nan), np.nan))
        lows.append(_safe_float(getattr(b, "low", np.nan), np.nan))

    highs = [x for x in highs if np.isfinite(x)]
    lows = [x for x in lows if np.isfinite(x)]

    if not highs or not lows:
        exit_price = _safe_float(getattr(tr, "exit_price", entry_price))
        pnl = int((exit_price - entry_price) * qty)
        return (max(pnl, 0), min(pnl, 0))

    max_high = float(max(highs))
    min_low = float(min(lows))

    mfe_yen = int((max_high - entry_price) * qty)
    mae_yen = int((min_low - entry_price) * qty)
    return (mfe_yen, mae_yen)


def _percentiles(xs: List[float], ps: List[int]) -> Dict[str, float]:
    if not xs:
        return {str(p): 0.0 for p in ps}
    arr = np.array(xs, dtype="float64")
    out = {}
    for p in ps:
        try:
            out[str(p)] = float(np.percentile(arr, p))
        except Exception:
            out[str(p)] = 0.0
    return out


# =========================================================
# 自動選定：共通ユーティリティ
# =========================================================

def _df_has_intraday_bars(df: pd.DataFrame, min_rows: int = 30) -> bool:
    """
    daytrade 5m df が「それっぽく取れてる」かの最低限チェック。
    5分足は1日で 70本前後（場中のみ）なので、30本あれば “取れてる” 扱いにする。
    """
    if df is None or df.empty:
        return False
    try:
        return int(len(df)) >= int(min_rows)
    except Exception:
        return False


def _try_fetch_5m_for_dates(
    code: str,
    check_dates: List[date],
    *,
    min_ok: int = 1,
    force_refresh: bool = True,
    min_rows: int = 30,
) -> bool:
    """
    指定銘柄について、check_dates のうち min_ok 日以上 “5分足が取れた” なら True。
    ※キャッシュ前提にしないため、デフォルトで force_refresh=True（必要なら取りに行く）
    """
    ok = 0
    for d in check_dates:
        try:
            df = load_daytrade_5m_bars(code, d, force_refresh=bool(force_refresh))
            if _df_has_intraday_bars(df, min_rows=min_rows):
                ok += 1
        except Exception:
            continue
        if ok >= int(min_ok):
            return True
    return False


def _liquidity_score_from_daytrade_df(df: pd.DataFrame) -> float:
    """
    load_daytrade_5m_bars の戻り（dt/open/high/low/close/volume/vwap）前提で
    close*volume 合計を流動性スコアにする。
    """
    if df is None or df.empty:
        return 0.0
    if "close" not in df.columns or "volume" not in df.columns:
        return 0.0
    try:
        close = pd.to_numeric(df["close"], errors="coerce")
        vol = pd.to_numeric(df["volume"], errors="coerce")
        x = (close * vol).replace([np.inf, -np.inf], np.nan).dropna()
        return float(x.sum()) if len(x) else 0.0
    except Exception:
        return 0.0


# =========================================================
# 自動選定（StockMasterあり版）
# =========================================================

def _get_all_jpx_codes_from_master() -> List[str]:
    if StockMaster is None:
        raise RuntimeError("StockMaster が利用できません（import失敗）。")
    qs = StockMaster.objects.all().values_list("code", flat=True)
    codes = []
    for c in qs:
        s = str(c).strip()
        if s:
            codes.append(s)
    return codes


def _master_field_exists(name: str) -> bool:
    if StockMaster is None:
        return False
    try:
        StockMaster._meta.get_field(name)  # type: ignore
        return True
    except Exception:
        return False


def _filter_universe_by_master_fields(codes: List[str], cfg: UniverseFilterConfig) -> List[str]:
    """
    picks_filter.filter_universe_codes は “内部で aiapp.models import StockMaster” をしていて
    export 状態に依存しやすいので、ここではこのスクリプト側の StockMaster を直接使って
    フィルタする（安定優先）。
    """
    if StockMaster is None or not codes:
        return list(codes)

    # 取得できそうなフィールド候補
    price_fields = ["last_price", "close_price", "price"]
    mcap_field = "market_cap"
    avg_val_fields = ["avg_trading_value_20d", "avg_value_20d", "avg_trading_value"]

    pfield = None
    for f in price_fields:
        if _master_field_exists(f):
            pfield = f
            break

    avfield = None
    for f in avg_val_fields:
        if _master_field_exists(f):
            avfield = f
            break

    has_mcap = _master_field_exists(mcap_field)

    # values() で引く（未知フィールドは外す）
    want = ["code"]
    if pfield:
        want.append(pfield)
    if has_mcap:
        want.append(mcap_field)
    if avfield:
        want.append(avfield)

    rows = list(StockMaster.objects.filter(code__in=codes).values(*want))

    keep: List[str] = []
    for r in rows:
        code = str(r.get("code", "")).strip()
        if not code:
            continue

        price = None
        if pfield:
            try:
                v = r.get(pfield)
                price = float(v) if v is not None else None
            except Exception:
                price = None

        mcap = None
        if has_mcap:
            try:
                v = r.get(mcap_field)
                mcap = float(v) if v is not None else None
            except Exception:
                mcap = None

        avg_val = None
        if avfield:
            try:
                v = r.get(avfield)
                avg_val = float(v) if v is not None else None
            except Exception:
                avg_val = None

        if price is not None and price < float(cfg.min_price):
            continue
        if mcap is not None and mcap < float(cfg.min_market_cap):
            continue
        if avg_val is not None and avg_val < float(cfg.min_avg_trading_value):
            continue

        keep.append(code)

    # マスタに存在しなかった銘柄は落とす（運用安定：変な銘柄を混ぜない）
    keep_set = set(keep)
    return [c for c in codes if c in keep_set]


def _rank_codes_by_avg_trading_value_master(codes: List[str]) -> List[str]:
    """StockMaster の平均売買代金系フィールドで降順ソート。無ければ code 順。"""
    if StockMaster is None or not codes:
        return list(codes)

    cand_fields = ["avg_trading_value_20d", "avg_value_20d", "avg_trading_value"]
    field = None
    for f in cand_fields:
        if _master_field_exists(f):
            field = f
            break

    if field is None:
        return sorted(codes)

    rows = list(StockMaster.objects.filter(code__in=codes).values("code", field))
    score = {}
    for r in rows:
        code = str(r.get("code", "")).strip()
        v = r.get(field)
        try:
            fv = float(v) if v is not None else 0.0
        except Exception:
            fv = 0.0
        score[code] = fv

    return sorted(codes, key=lambda c: score.get(c, 0.0), reverse=True)


def auto_select_daytrade_tickers_master(
    *,
    top_n: int,
    cfg: UniverseFilterConfig,
    data_check_days: int = 1,
    data_check_min_ok: int = 1,
    pre_rank_pool: int = 600,
    force_refresh: bool = True,
    min_rows: int = 30,
) -> List[str]:
    """
    理想ルート：
    1) StockMaster でユニバースを “デイトレ向き” に絞る（価格/時価総額/平均売買代金）
    2) 平均売買代金の上位から pre_rank_pool だけ見る
    3) その中で必要な銘柄だけ 5分足を force_refresh=True で取得トライして top_n を埋める
    """
    all_codes = _get_all_jpx_codes_from_master()
    filtered = _filter_universe_by_master_fields(all_codes, cfg)
    ranked = _rank_codes_by_avg_trading_value_master(filtered)

    ranked = ranked[: max(int(pre_rank_pool), int(top_n))]

    # “直近数日” を見るより、まずは “直近1日取れるか” を優先（運用安定・速度優先）
    check_dates = _last_n_bdays_jst(max(int(data_check_days), 1))

    kept: List[str] = []
    for c in ranked:
        if _try_fetch_5m_for_dates(
            c,
            check_dates,
            min_ok=int(data_check_min_ok),
            force_refresh=bool(force_refresh),
            min_rows=int(min_rows),
        ):
            kept.append(c)
        if len(kept) >= int(top_n):
            break

    return kept


# =========================================================
# 自動選定（フォールバック版：tse_list.csv + 5分足実データ流動性）
# =========================================================

def _load_codes_from_tse_list_csv() -> List[str]:
    """
    portfolio/data/tse_list.csv から銘柄コードをできるだけ頑丈に抽出する。
    形式が多少違っても、4桁数字っぽいものを拾う。
    """
    p = Path("portfolio") / "data" / "tse_list.csv"
    if not p.exists():
        raise RuntimeError(f"tse_list.csv が見つかりません: {p}")

    text = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    codes: List[str] = []
    pat = re.compile(r"\b(\d{4})\b")
    for line in text:
        m = pat.search(line)
        if not m:
            continue
        codes.append(m.group(1))

    seen = set()
    out = []
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def auto_select_daytrade_tickers_fallback(
    *,
    top_n: int,
    data_check_days: int = 1,
    data_check_min_ok: int = 1,
    scan_limit: int = 2000,
    force_refresh: bool = True,
    min_rows: int = 30,
) -> List[str]:
    """
    StockMasterが無くても回る自動選定（ただし yfinance 取得負荷は上がる）。

    方針（運用安定・速度優先）：
    - tse_list.csv を scan_limit まで見る
    - 直近 data_check_days 日だけ “force_refresh=True で取得トライ”
    - 取れた銘柄を流動性（close*volume）上位から top_n 返す

    ※このルートは「StockMaster が無い非常時」の保険。通常は master ルート推奨。
    """
    codes = _load_codes_from_tse_list_csv()
    if not codes:
        return []

    check_dates = _last_n_bdays_jst(max(int(data_check_days), 1))

    scored: List[Tuple[float, str]] = []
    scanned = 0

    for c in codes:
        scanned += 1
        if int(scan_limit) > 0 and scanned > int(scan_limit):
            break

        ok_days = 0
        score_sum = 0.0

        for d in check_dates:
            try:
                df = load_daytrade_5m_bars(c, d, force_refresh=bool(force_refresh))
            except Exception:
                df = None

            if not _df_has_intraday_bars(df if df is not None else pd.DataFrame(), min_rows=int(min_rows)):
                continue

            ok_days += 1
            score_sum += _liquidity_score_from_daytrade_df(df)

        if ok_days >= int(data_check_min_ok):
            score = float(score_sum) / max(int(ok_days), 1)
            scored.append((score, c))

    scored.sort(reverse=True, key=lambda x: x[0])
    return [c for _, c in scored[: int(top_n)]]


# =========================================================
# Backtest runner
# =========================================================

def run_for_ticker(
    ticker: str,
    dates: List[date],
    policy: dict,
    budget_trade_loss_yen: int,
    exit_stats: Dict[str, Any],
) -> Agg:
    agg = Agg(max_dd_yen=0)

    for d in dates:
        # backtest 本体は “キャッシュ優先” で安定運用（必要なら別スクリプトで prefetch してもOK）
        df = load_daytrade_5m_bars(ticker, d, force_refresh=False)
        if df is None or df.empty:
            continue

        bars = df_to_bars_5m(df)
        if not bars:
            continue

        res = run_backtest_one_day(bars=bars, policy=policy)
        _update_agg(agg, res)

        for tr in getattr(res, "trades", []):
            reason = _get_exit_reason(tr)
            pnl = int(getattr(tr, "pnl_yen", 0))
            r = float(getattr(tr, "r", 0.0))

            entry_dt = getattr(tr, "entry_dt", None)
            exit_dt = getattr(tr, "exit_dt", None)

            held_min = 0.0
            try:
                if entry_dt is not None and exit_dt is not None:
                    held_min = float((exit_dt - entry_dt).total_seconds() / 60.0)
            except Exception:
                held_min = 0.0

            bars_slice = []
            try:
                if entry_dt is not None and exit_dt is not None:
                    bars_slice = _slice_bars_for_trade(bars, entry_dt, exit_dt)
            except Exception:
                bars_slice = []

            mfe_yen, mae_yen = _trade_mfe_mae_yen_long(tr, bars_slice)

            denom = max(int(budget_trade_loss_yen), 1)
            mfe_r = float(mfe_yen) / float(denom)
            mae_r = float(mae_yen) / float(denom)

            slot = exit_stats.setdefault(
                reason,
                {
                    "trades": 0,
                    "wins": 0,
                    "pnl": 0,
                    "sum_r": 0.0,
                    "held_minutes": [],
                    "mfe_r": [],
                    "mae_r": [],
                },
            )
            slot["trades"] += 1
            slot["pnl"] += pnl
            slot["sum_r"] += float(r)
            if pnl >= 0:
                slot["wins"] += 1
            slot["held_minutes"].append(float(held_min))
            slot["mfe_r"].append(float(mfe_r))
            slot["mae_r"].append(float(mae_r))

    return agg


def _report_dir_today() -> Path:
    d = date.today().strftime("%Y%m%d")
    p = Path(settings.MEDIA_ROOT) / "aiapp" / "daytrade" / "reports" / d
    p.mkdir(parents=True, exist_ok=True)
    return p


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("n", type=int, help="過去N営業日（20/60/120）")
    p.add_argument("tickers", nargs="*", help="手動指定の銘柄コード（省略可）")

    p.add_argument("--auto", action="store_true", help="JPX全銘柄から自動選定して回す")
    p.add_argument("--top", type=int, default=40, help="自動選定で使う銘柄数（上位N）")

    # ユニバースフィルタ閾値（StockMasterが使える場合に効く）
    p.add_argument("--min-price", type=float, default=300.0, help="ユニバース: 最低株価")
    p.add_argument("--min-mcap", type=float, default=20_000_000_000.0, help="ユニバース: 最低時価総額")
    p.add_argument("--min-avg-value", type=float, default=50_000_000.0, help="ユニバース: 最低平均売買代金（20d想定）")

    # 5分足データ存在チェック
    p.add_argument("--data-check-days", type=int, default=1, help="直近何営業日で5分足の取得可否をチェックするか")
    p.add_argument("--data-check-min-ok", type=int, default=1, help="何日分取れたら採用とするか")
    p.add_argument("--min-rows", type=int, default=30, help="5分足が取れた判定の最低行数（デフォルト30本）")

    # StockMasterあり版の高速化
    p.add_argument("--pre-rank-pool", type=int, default=600, help="（StockMasterあり）流動性順に上位何銘柄まで絞ってから取得トライするか")

    # フォールバック版の高速化
    p.add_argument("--scan-limit", type=int, default=2000, help="（StockMasterなし）tse_list.csv を先頭から何銘柄スキャンするか（0=無制限）")

    # auto で “取りに行く” を切り替えたい時用（通常はON推奨）
    p.add_argument("--no-force-refresh", action="store_true", help="auto選定時に force_refresh=True で取りに行かない（キャッシュ前提）")

    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()

    n = int(args.n)
    if n not in (20, 60, 120):
        print("N must be one of 20/60/120")
        sys.exit(1)

    tickers = [str(x).strip() for x in (args.tickers or []) if str(x).strip()]

    if not tickers:
        if not bool(args.auto):
            print("tickers is empty. 手動指定するか、--auto を付けて自動選定してください。")
            sys.exit(1)

        top_n = max(int(args.top), 1)
        force_refresh = (not bool(args.no_force_refresh))

        cfg = UniverseFilterConfig(
            min_price=float(args.min_price),
            min_market_cap=float(args.min_mcap),
            min_avg_trading_value=float(args.min_avg_value),
            allowed_markets=None,
        )

        src = "fallback"
        if StockMaster is not None:
            try:
                tickers = auto_select_daytrade_tickers_master(
                    top_n=top_n,
                    cfg=cfg,
                    data_check_days=int(args.data_check_days),
                    data_check_min_ok=int(args.data_check_min_ok),
                    pre_rank_pool=int(args.pre_rank_pool),
                    force_refresh=bool(force_refresh),
                    min_rows=int(args.min_rows),
                )
                src = "master"
            except Exception as e:
                print("auto selection (master) failed:", e)
                tickers = []
                src = "fallback"

        if not tickers:
            try:
                tickers = auto_select_daytrade_tickers_fallback(
                    top_n=top_n,
                    data_check_days=int(args.data_check_days),
                    data_check_min_ok=int(args.data_check_min_ok),
                    scan_limit=int(args.scan_limit),
                    force_refresh=bool(force_refresh),
                    min_rows=int(args.min_rows),
                )
                src = "fallback"
            except Exception as e:
                print("auto selection (fallback) failed:", e)
                sys.exit(1)

        if not tickers:
            print("auto selection result is empty. データ不足 or scan_limit が小さすぎる可能性があります。")
            sys.exit(1)

        print("=== auto selected tickers ===")
        print("source =", src)
        print("top_n =", top_n)
        print("selected =", tickers)
        print("")

    policy = load_policy_yaml().policy
    dates = _last_n_bdays_jst(n)

    capital_cfg = policy.get("capital", {})
    risk_cfg = policy.get("risk", {})
    base_capital = int(capital_cfg.get("base_capital", 0))
    trade_loss_pct = float(risk_cfg.get("trade_loss_pct", 0.0))
    day_loss_pct = float(risk_cfg.get("day_loss_pct", 0.0))
    budget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)
    budget_trade_loss_yen = int(getattr(budget, "trade_loss_yen", 1))
    budget_trade_loss_yen = max(budget_trade_loss_yen, 1)

    print("=== daytrade backtest multi (simple) ===")
    print("policy_id =", policy.get("meta", {}).get("policy_id"))
    print("days (bday approx) =", n)
    print("tickers =", tickers)
    print("")

    total = Agg(max_dd_yen=0)
    exit_stats: Dict[str, Any] = {}

    for t in tickers:
        agg = run_for_ticker(
            ticker=t,
            dates=dates,
            policy=policy,
            budget_trade_loss_yen=budget_trade_loss_yen,
            exit_stats=exit_stats,
        )

        trades = agg.total_trades
        avg_r = (agg.sum_r / trades) if trades > 0 else 0.0
        winrate = (agg.wins / trades) if trades > 0 else 0.0

        print(
            f"[{t}] used_days={agg.days} traded_days={agg.traded_days} trades={trades} pnl={agg.total_pnl} "
            f"winrate={_fmt_pct(winrate)} avg_r={avg_r:.4f} max_dd_yen={agg.max_dd_yen}"
        )

        total.days += agg.days
        total.traded_days += agg.traded_days
        total.total_trades += agg.total_trades
        total.total_pnl += agg.total_pnl
        total.sum_r += agg.sum_r
        total.wins += agg.wins
        total.losses += agg.losses
        total.max_dd_yen = min(total.max_dd_yen, agg.max_dd_yen)

    trades = total.total_trades
    avg_r = (total.sum_r / trades) if trades > 0 else 0.0
    winrate = (total.wins / trades) if trades > 0 else 0.0

    print("")
    print("---- total ----")
    print(
        f"used_days={total.days} traded_days={total.traded_days} trades={trades} pnl={total.total_pnl} "
        f"winrate={_fmt_pct(winrate)} avg_r={avg_r:.4f} max_dd_yen={total.max_dd_yen}"
    )

    print("")
    print("---- exit_reason breakdown (total) ----")

    items = []
    for reason, st in exit_stats.items():
        tcnt = int(st.get("trades", 0))
        if tcnt <= 0:
            continue
        items.append((tcnt, reason, st))
    items.sort(reverse=True, key=lambda x: x[0])

    breakdown_rows = []
    for tcnt, reason, st in items:
        wins_r = int(st.get("wins", 0))
        pnl_r = int(st.get("pnl", 0))
        sum_r = float(st.get("sum_r", 0.0))
        winrate_r = (wins_r / tcnt) if tcnt > 0 else 0.0
        avg_r_reason = (sum_r / tcnt) if tcnt > 0 else 0.0

        held = list(st.get("held_minutes", [])) or []
        mfe_r = list(st.get("mfe_r", [])) or []
        mae_r = list(st.get("mae_r", [])) or []

        avg_held = float(np.mean(held)) if held else 0.0
        avg_mfe_r = float(np.mean(mfe_r)) if mfe_r else 0.0
        avg_mae_r = float(np.mean(mae_r)) if mae_r else 0.0

        print(
            f"{reason:28s} trades={tcnt:4d} winrate={winrate_r*100:5.1f}% pnl={pnl_r:8d} avg_r={avg_r_reason:7.4f} "
            f"avg_hold_min={avg_held:5.1f} avg_mfe_r={avg_mfe_r:6.3f} avg_mae_r={avg_mae_r:6.3f}"
        )

        breakdown_rows.append(
            {
                "exit_reason": reason,
                "trades": tcnt,
                "wins": wins_r,
                "winrate": winrate_r,
                "pnl": pnl_r,
                "avg_r": avg_r_reason,
                "avg_hold_min": avg_held,
                "avg_mfe_r": avg_mfe_r,
                "avg_mae_r": avg_mae_r,
            }
        )

    out_dir = _report_dir_today()
    out_path = out_dir / "exit_breakdown.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "policy_id": policy.get("meta", {}).get("policy_id"),
        "n_bdays_approx": n,
        "tickers": tickers,
        "total": {
            "used_days": total.days,
            "traded_days": total.traded_days,
            "trades": total.total_trades,
            "pnl": total.total_pnl,
            "winrate": winrate,
            "avg_r": avg_r,
            "max_dd_yen": total.max_dd_yen,
            "budget_trade_loss_yen": budget_trade_loss_yen,
        },
        "breakdown": breakdown_rows,
    }
    try:
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("")
        print("saved exit breakdown = " + str(out_path))
    except Exception as e:
        print("")
        print("failed to save exit breakdown:", e)

    print("=== done ===")


if __name__ == "__main__":
    main()