# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Tuple
from datetime import date

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

import numpy as np
import pandas as pd

from aiapp.services.daytrade.policy_loader import load_policy_yaml
from aiapp.services.daytrade.bars_5m_daytrade import load_daytrade_5m_bars
from aiapp.services.daytrade.bar_adapter_5m import df_to_bars_5m
from aiapp.services.daytrade.backtest_runner import run_backtest_one_day
from aiapp.services.daytrade.risk_math import calc_risk_budget_yen


# 開発用：まずは「これだけで開発してOK」な固定リスト
DEV_DEFAULT_TICKERS = ["7203", "6758", "9984", "8306", "8316", "8035", "6861", "6501", "9432", "6098"]


def _last_n_bdays_jst(n: int, end_d: date | None = None) -> List[date]:
    if end_d is None:
        end_d = date.today()
    ds = pd.bdate_range(end=end_d, periods=n).to_pydatetime()
    return [d.date() for d in ds]


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


def _slice_bars_for_trade(bars, entry_dt, exit_dt):
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


def _parse_tickers(text: str) -> List[str]:
    s = (text or "").replace(",", " ").replace("\n", " ")
    xs = [x.strip() for x in s.split(" ") if x.strip()]
    # 4〜5桁数字っぽいのだけ残す（.Tはここでは入れない）
    out = []
    for x in xs:
        if x.isdigit() and (4 <= len(x) <= 5):
            out.append(x)
    # 重複除去（順序維持）
    seen = set()
    uniq = []
    for c in out:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)
    return uniq


def daytrade_backtest_view(request: HttpRequest) -> HttpResponse:
    # ---------- form defaults ----------
    form_n = 20
    form_mode = "dev_default"
    form_tickers = ""
    form_top = 40
    form_scan_limit = 2000
    form_pre_rank_pool = 400

    run_log_lines: List[str] = []

    selected_tickers: List[str] = []
    rows: List[Dict[str, Any]] = []
    exit_rows: List[Dict[str, Any]] = []

    kpi_total_pnl = 0
    kpi_trades = 0
    kpi_winrate = "-"
    kpi_avg_r = "-"
    kpi_max_dd = 0

    # ---------- policy ----------
    try:
        loaded = load_policy_yaml()
        policy = loaded.policy
        policy_id = loaded.policy_id
    except Exception as e:
        policy = {}
        policy_id = ""
        run_log_lines.append(f"[error] policy load failed: {e}")

    # budget (for MFE/MAE R conversion)
    capital_cfg = (policy or {}).get("capital", {})
    risk_cfg = (policy or {}).get("risk", {})
    base_capital = int(capital_cfg.get("base_capital", 0) or 0)
    trade_loss_pct = float(risk_cfg.get("trade_loss_pct", 0.0) or 0.0)
    day_loss_pct = float(risk_cfg.get("day_loss_pct", 0.0) or 0.0)
    budget = calc_risk_budget_yen(base_capital, trade_loss_pct, day_loss_pct)
    budget_trade_loss_yen = max(int(getattr(budget, "trade_loss_yen", 1)), 1)

    if request.method == "POST":
        # ---------- read form ----------
        try:
            form_n = int(request.POST.get("n") or 20)
        except Exception:
            form_n = 20

        form_mode = str(request.POST.get("mode") or "dev_default").strip()
        form_tickers = str(request.POST.get("tickers") or "")

        try:
            form_top = int(request.POST.get("top") or 40)
        except Exception:
            form_top = 40

        try:
            form_scan_limit = int(request.POST.get("scan_limit") or 2000)
        except Exception:
            form_scan_limit = 2000

        try:
            form_pre_rank_pool = int(request.POST.get("pre_rank_pool") or 400)
        except Exception:
            form_pre_rank_pool = 400

        if form_n not in (20, 60, 120):
            form_n = 20

        # ---------- select tickers ----------
        if form_mode == "manual":
            selected_tickers = _parse_tickers(form_tickers)
            if not selected_tickers:
                selected_tickers = DEV_DEFAULT_TICKERS[:]
                run_log_lines.append("[warn] manual tickers empty -> fallback to dev_default")
        elif form_mode == "auto":
            # ここは「本番向け候補JSON」が出来たら差し替える
            # 今は速度優先で dev_default に落とす（UIは先に完成させる）
            selected_tickers = DEV_DEFAULT_TICKERS[:]
            run_log_lines.append("[info] auto is not wired yet -> using dev_default (dev speed first)")
        else:
            selected_tickers = DEV_DEFAULT_TICKERS[:]

        # 安全装置：画面からの実行は最大10銘柄まで（開発速度最優先）
        if len(selected_tickers) > 10:
            selected_tickers = selected_tickers[:10]
            run_log_lines.append("[info] tickers capped to 10 for dev speed")

        # ---------- run backtest ----------
        dates = _last_n_bdays_jst(form_n)
        exit_stats: Dict[str, Any] = {}

        total_days = 0
        total_traded_days = 0
        total_trades = 0
        total_pnl = 0
        total_sum_r = 0.0
        total_wins = 0
        total_losses = 0
        total_max_dd = 0  # min negative

        run_log_lines.append("=== daytrade backtest (ui) ===")
        run_log_lines.append(f"policy_id = {policy_id}")
        run_log_lines.append(f"n = {form_n}")
        run_log_lines.append(f"tickers = {selected_tickers}")

        for t in selected_tickers:
            used_days = 0
            traded_days = 0
            trades_cnt = 0
            pnl_sum = 0
            sum_r = 0.0
            wins = 0
            losses = 0
            max_dd = 0

            for d in dates:
                df = load_daytrade_5m_bars(t, d, force_refresh=False)
                if df is None or df.empty:
                    continue

                bars = df_to_bars_5m(df)
                if not bars:
                    continue

                used_days += 1
                res = run_backtest_one_day(bars=bars, policy=policy)

                day_trades = list(getattr(res, "trades", []) or [])
                pnl_sum += int(getattr(res, "pnl_yen", 0) or 0)
                trades_cnt += int(len(day_trades))
                if len(day_trades) > 0:
                    traded_days += 1

                try:
                    max_dd = min(int(max_dd), int(getattr(res, "max_drawdown_yen", 0) or 0))
                except Exception:
                    pass

                for tr in day_trades:
                    r = float(getattr(tr, "r", 0.0) or 0.0)
                    sum_r += r
                    if int(getattr(tr, "pnl_yen", 0) or 0) >= 0:
                        wins += 1
                    else:
                        losses += 1

                    reason = _get_exit_reason(tr)
                    pnl = int(getattr(tr, "pnl_yen", 0) or 0)

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

            avg_r = (sum_r / trades_cnt) if trades_cnt > 0 else 0.0
            winrate = (wins / trades_cnt) if trades_cnt > 0 else 0.0

            rows.append(
                {
                    "ticker": t,
                    "used_days": used_days,
                    "traded_days": traded_days,
                    "trades": trades_cnt,
                    "pnl": pnl_sum,
                    "winrate": _fmt_pct(winrate) if trades_cnt > 0 else "0.0%",
                    "avg_r": f"{avg_r:.4f}",
                    "max_dd_yen": max_dd,
                    "_avg_r_num": float(avg_r),
                }
            )

            run_log_lines.append(
                f"[{t}] used_days={used_days} traded_days={traded_days} trades={trades_cnt} pnl={pnl_sum} "
                f"winrate={_fmt_pct(winrate)} avg_r={avg_r:.4f} max_dd_yen={max_dd}"
            )

            total_days += used_days
            total_traded_days += traded_days
            total_trades += trades_cnt
            total_pnl += pnl_sum
            total_sum_r += float(sum_r)
            total_wins += wins
            total_losses += losses
            total_max_dd = min(int(total_max_dd), int(max_dd))

        # KPIs
        total_avg_r = (total_sum_r / total_trades) if total_trades > 0 else 0.0
        total_winrate = (total_wins / total_trades) if total_trades > 0 else 0.0

        kpi_total_pnl = int(total_pnl)
        kpi_trades = int(total_trades)
        kpi_winrate = _fmt_pct(float(total_winrate)) if total_trades > 0 else "0.0%"
        kpi_avg_r = f"{float(total_avg_r):.4f}"
        kpi_max_dd = int(total_max_dd)

        run_log_lines.append("")
        run_log_lines.append("---- total ----")
        run_log_lines.append(
            f"used_days={total_days} traded_days={total_traded_days} trades={total_trades} pnl={total_pnl} "
            f"winrate={_fmt_pct(total_winrate)} avg_r={total_avg_r:.4f} max_dd_yen={total_max_dd}"
        )

        # exit_rows
        items = []
        for reason, st in exit_stats.items():
            tcnt = int(st.get("trades", 0) or 0)
            if tcnt <= 0:
                continue
            items.append((tcnt, reason, st))
        items.sort(reverse=True, key=lambda x: x[0])

        for tcnt, reason, st in items:
            wins_r = int(st.get("wins", 0) or 0)
            pnl_r = int(st.get("pnl", 0) or 0)
            sum_r = float(st.get("sum_r", 0.0) or 0.0)

            winrate_r = (wins_r / tcnt) if tcnt > 0 else 0.0
            avg_r_reason = (sum_r / tcnt) if tcnt > 0 else 0.0

            held = list(st.get("held_minutes", []) or [])
            mfe_r = list(st.get("mfe_r", []) or [])
            mae_r = list(st.get("mae_r", []) or [])

            avg_held = float(np.mean(held)) if held else 0.0
            avg_mfe_r = float(np.mean(mfe_r)) if mfe_r else 0.0
            avg_mae_r = float(np.mean(mae_r)) if mae_r else 0.0

            exit_rows.append(
                {
                    "exit_reason": reason,
                    "trades": int(tcnt),
                    "wins": int(wins_r),
                    "winrate": _fmt_pct(float(winrate_r)),
                    "pnl": int(pnl_r),
                    "avg_r": float(round(avg_r_reason, 4)),
                    "avg_hold_min": float(round(avg_held, 1)),
                    "avg_mfe_r": float(round(avg_mfe_r, 3)),
                    "avg_mae_r": float(round(avg_mae_r, 3)),
                    "_avg_r_num": float(avg_r_reason),
                }
            )

    ctx = {
        # form echo
        "form_n": form_n,
        "form_mode": form_mode,
        "form_tickers": form_tickers,
        "form_top": form_top,
        "form_scan_limit": form_scan_limit,
        "form_pre_rank_pool": form_pre_rank_pool,
        # meta
        "policy_id": policy_id,
        "budget_trade_loss_yen": budget_trade_loss_yen,
        # outputs
        "selected_tickers": selected_tickers,
        "rows": rows,
        "exit_rows": exit_rows,
        "run_log": "\n".join(run_log_lines) if run_log_lines else "（ここにログが出る）",
        # KPIs
        "kpi_total_pnl": kpi_total_pnl,
        "kpi_trades": kpi_trades,
        "kpi_winrate": kpi_winrate,
        "kpi_avg_r": kpi_avg_r,
        "kpi_max_dd": kpi_max_dd,
    }
    return render(request, "aiapp/daytrade_backtest.html", ctx)