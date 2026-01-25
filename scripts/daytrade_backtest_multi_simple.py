# -*- coding: utf-8 -*-
"""
ファイル: scripts/daytrade_backtest_multi_simple.py

目的（かんたんテスト / ワンタップ）
- 複数銘柄 × 過去N営業日（20/60/120）で、デイトレ5分足バックテストを回す。
- 戦略ロジックは一切変えない（既存: VWAPPullbackLongStrategy のまま）。
- 0トレの日が出るのは仕様。銘柄数を増やして「回る」ようにする。

【追加: 自動銘柄選定（JPX全銘柄→フィルタ→上位N）】
- tickers を渡さずに --auto を指定すると、
  StockMaster から JPX 全銘柄を読み、ユニバースフィルタを通し、
  さらに「5分足データが取れる銘柄だけ」に絞って上位N銘柄を選定して回す。

実行例:
  # 従来（手動）
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_backtest_multi_simple.py 20 3023 6946 9501

  # 自動（全銘柄→フィルタ→流動性上位40）
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_backtest_multi_simple.py 20 --auto --top 40

  # 自動（フィルタ閾値も指定）
  PYTHONPATH=. DJANGO_SETTINGS_MODULE=config.settings python scripts/daytrade_backtest_multi_simple.py 20 --auto --top 60 \
    --min-price 300 --min-mcap 20000000000 --min-avg-value 200000000

出力:
- 銘柄別サマリ
- 全体サマリ（勝率/avgR/DD/総トレ/総PnL）
- exit_reason breakdown（理由別の集計）
- exit_reasonごとの「保有時間・MFE/MAE（R換算）」の追加診断（運用品質）
  ※特に time_limit の中身が見えるようにする

保存:
- media/aiapp/daytrade/reports/YYYYMMDD/exit_breakdown.json
- media/aiapp/daytrade/reports/YYYYMMDD/trades_detail.json（※別実装が既に入ってる前提）
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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

# --- 追加: ユニバースフィルタ（picks_filter を流用） ---
from aiapp.services.picks_filter import (
    UniverseFilterConfig,
    filter_universe_codes,
)

try:
    from aiapp.models import StockMaster  # type: ignore
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
    """
    過去N営業日（簡易：平日のみ）。
    ※JPX祝日は未考慮（まず“回す”ことを優先）。
    """
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
    """
    entry_dt〜exit_dt の間のバーを抽出。
    bars は types.Bar のリスト想定（dt/high/low/closeなど持つ）
    """
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
    """
    ロング前提で MFE/MAE を円で計算。
    - MFE: 最大含み益（最大高値 - entry_price）* qty
    - MAE: 最大逆行（最小安値 - entry_price）* qty  ※負値になりやすい
    """
    entry_price = _safe_float(getattr(tr, "entry_price", 0.0))
    qty = _safe_int(getattr(tr, "qty", 0))
    if qty <= 0 or entry_price <= 0:
        return (0, 0)

    # スライスが無い場合は、約定値だけで近似
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
# 追加: 自動銘柄選定（JPX全銘柄→フィルタ→データ有→流動性上位）
# =========================================================

def _get_all_jpx_codes_from_master() -> List[str]:
    if StockMaster is None:
        raise RuntimeError("StockMaster が import できません。全銘柄自動選定には StockMaster が必要です。")
    qs = StockMaster.objects.all().values_list("code", flat=True)
    codes = []
    for c in qs:
        s = str(c).strip()
        if s:
            codes.append(s)
    return codes


def _rank_codes_by_avg_trading_value(codes: List[str]) -> List[str]:
    """
    StockMaster の avg_trading_value_20d / avg_value_20d / avg_trading_value を使って
    流動性の高い順に並べる。フィールドが無い場合は code 順で返す。
    """
    if StockMaster is None or not codes:
        return list(codes)

    # 候補フィールドを順に試す
    cand_fields = ["avg_trading_value_20d", "avg_value_20d", "avg_trading_value"]
    field = None
    for f in cand_fields:
        try:
            StockMaster._meta.get_field(f)  # type: ignore
            field = f
            break
        except Exception:
            continue

    if field is None:
        return sorted(codes)

    # NULL は後ろに行くようにする（Django DB差異があるので Python 側で安定化）
    rows = list(
        StockMaster.objects.filter(code__in=codes).values("code", field)
    )
    score = {}
    for r in rows:
        code = str(r.get("code", "")).strip()
        v = r.get(field)
        try:
            fv = float(v) if v is not None else 0.0
        except Exception:
            fv = 0.0
        score[code] = fv

    # マスタに無いコードは最後に回す
    return sorted(codes, key=lambda c: score.get(c, 0.0), reverse=True)


def _has_enough_5m_data(ticker: str, check_dates: List[date], min_days_ok: int = 2) -> bool:
    """
    指定された複数日で 5分足が取れる日が min_days_ok 以上あれば OK とみなす。
    （完全厳密より「運用で回る」優先）
    """
    ok = 0
    for d in check_dates:
        try:
            df = load_daytrade_5m_bars(ticker, d, force_refresh=False)
            if df is not None and not df.empty:
                ok += 1
        except Exception:
            continue
        if ok >= min_days_ok:
            return True
    return False


def auto_select_daytrade_tickers(
    *,
    top_n: int,
    cfg: UniverseFilterConfig,
    data_check_days: int = 3,
    data_check_min_ok: int = 2,
    pre_rank_pool: int = 400,
) -> List[str]:
    """
    1) StockMaster から全銘柄取得
    2) ユニバースフィルタ（picks_filter）適用
    3) 流動性順に並べて上位 pre_rank_pool まで縮小（高速化）
    4) 直近数営業日で 5分足が取れる銘柄だけ残す
    5) 上位 top_n を返す
    """
    all_codes = _get_all_jpx_codes_from_master()
    filtered = filter_universe_codes(all_codes, cfg)

    ranked = _rank_codes_by_avg_trading_value(filtered)
    if pre_rank_pool > 0:
        ranked = ranked[: max(int(pre_rank_pool), int(top_n))]

    check_dates = _last_n_bdays_jst(max(int(data_check_days), 1))
    kept = []
    for c in ranked:
        if _has_enough_5m_data(c, check_dates, min_days_ok=int(data_check_min_ok)):
            kept.append(c)
        if len(kept) >= int(top_n):
            break
    return kept


def run_for_ticker(
    ticker: str,
    dates: List[date],
    policy: dict,
    budget_trade_loss_yen: int,
    exit_stats: Dict[str, Any],
) -> Agg:
    agg = Agg(max_dd_yen=0)

    for d in dates:
        df = load_daytrade_5m_bars(ticker, d, force_refresh=False)
        if df is None or df.empty:
            continue

        bars = df_to_bars_5m(df)
        if not bars:
            continue

        res = run_backtest_one_day(bars=bars, policy=policy)
        _update_agg(agg, res)

        # --- exit_reason 診断用：トレードごとの詳細を蓄積 ---
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

            # MFE/MAE（ロング前提）
            bars_slice = []
            try:
                if entry_dt is not None and exit_dt is not None:
                    bars_slice = _slice_bars_for_trade(bars, entry_dt, exit_dt)
            except Exception:
                bars_slice = []

            mfe_yen, mae_yen = _trade_mfe_mae_yen_long(tr, bars_slice)

            # R換算（trade_loss_yen基準）
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

    # 自動選定
    p.add_argument("--auto", action="store_true", help="JPX全銘柄から自動選定して回す")
    p.add_argument("--top", type=int, default=40, help="自動選定で使う銘柄数（上位N）")

    # ユニバースフィルタ閾値（picks_filter をそのまま使う）
    p.add_argument("--min-price", type=float, default=300.0, help="ユニバース: 最低株価")
    p.add_argument("--min-mcap", type=float, default=20_000_000_000.0, help="ユニバース: 最低時価総額")
    p.add_argument("--min-avg-value", type=float, default=50_000_000.0, help="ユニバース: 最低平均売買代金（20d想定）")

    # 5分足データ存在チェック
    p.add_argument("--data-check-days", type=int, default=3, help="直近何営業日で5分足の存在をチェックするか")
    p.add_argument("--data-check-min-ok", type=int, default=2, help="何日分データが取れたら採用とするか")
    p.add_argument("--pre-rank-pool", type=int, default=400, help="流動性順に上位何銘柄まで絞ってからデータチェックするか")

    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()

    n = int(args.n)
    if n not in (20, 60, 120):
        print("N must be one of 20/60/120")
        sys.exit(1)

    # tickers が渡されていれば従来通り
    tickers = [str(x).strip() for x in (args.tickers or []) if str(x).strip()]

    # tickers が無い場合は --auto 必須
    if not tickers:
        if not bool(args.auto):
            print("tickers is empty. 手動指定するか、--auto を付けて自動選定してください。")
            sys.exit(1)

        cfg = UniverseFilterConfig(
            min_price=float(args.min_price),
            min_market_cap=float(args.min_mcap),
            min_avg_trading_value=float(args.min_avg_value),
            allowed_markets=None,
        )

        top_n = max(int(args.top), 1)
        try:
            tickers = auto_select_daytrade_tickers(
                top_n=top_n,
                cfg=cfg,
                data_check_days=int(args.data_check_days),
                data_check_min_ok=int(args.data_check_min_ok),
                pre_rank_pool=int(args.pre_rank_pool),
            )
        except Exception as e:
            print("auto selection failed:", e)
            sys.exit(1)

        if not tickers:
            print("auto selection result is empty. フィルタが厳しすぎるか、5分足データが不足しています。")
            sys.exit(1)

        print("=== auto selected tickers ===")
        print("top_n =", top_n)
        print("selected =", tickers)
        print("")

    policy = load_policy_yaml().policy
    dates = _last_n_bdays_jst(n)

    # budget（trade_loss_yen基準でR換算するため）
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

    # exit_reason別の詳細統計を全体で貯める
    exit_stats: Dict[str, Any] = {}

    # 銘柄別
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

        # 全体へ加算
        total.days += agg.days
        total.traded_days += agg.traded_days
        total.total_trades += agg.total_trades
        total.total_pnl += agg.total_pnl
        total.sum_r += agg.sum_r
        total.wins += agg.wins
        total.losses += agg.losses
        total.max_dd_yen = min(total.max_dd_yen, agg.max_dd_yen)

    # 全体
    trades = total.total_trades
    avg_r = (total.sum_r / trades) if trades > 0 else 0.0
    winrate = (total.wins / trades) if trades > 0 else 0.0

    print("")
    print("---- total ----")
    print(
        f"used_days={total.days} traded_days={total.traded_days} trades={trades} pnl={total.total_pnl} "
        f"winrate={_fmt_pct(winrate)} avg_r={avg_r:.4f} max_dd_yen={total.max_dd_yen}"
    )

    # ---- exit_reason breakdown（従来 + 強化）----
    print("")
    print("---- exit_reason breakdown (total) ----")

    # 表示順：見やすいように trades多い順
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

        p_held = _percentiles(held, [50, 75, 90])
        p_mfe = _percentiles(mfe_r, [50, 75, 90])
        p_mae = _percentiles(mae_r, [50, 75, 90])

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
                "p50_hold_min": p_held.get("50", 0.0),
                "p75_hold_min": p_held.get("75", 0.0),
                "p90_hold_min": p_held.get("90", 0.0),
                "p50_mfe_r": p_mfe.get("50", 0.0),
                "p75_mfe_r": p_mfe.get("75", 0.0),
                "p90_mfe_r": p_mfe.get("90", 0.0),
                "p50_mae_r": p_mae.get("50", 0.0),
                "p75_mae_r": p_mae.get("75", 0.0),
                "p90_mae_r": p_mae.get("90", 0.0),
            }
        )

    # 保存
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